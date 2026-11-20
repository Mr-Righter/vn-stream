// Minimal Linux window host for the pinned KrKr2-Next C ABI.
#include <SDL.h>
#include <gtk/gtk.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include "engine_api.h"
#include "engine_options.h"

namespace {
using Clock = std::chrono::steady_clock;

void check(engine_result_t result, engine_handle_t handle) {
    if (result != ENGINE_RESULT_OK)
        throw std::runtime_error(engine_get_last_error(handle));
}

int key_code(SDL_Keycode key) {
    if (key >= SDLK_a && key <= SDLK_z) return 'A' + key - SDLK_a;
    if (key >= SDLK_F1 && key <= SDLK_F12) return 112 + key - SDLK_F1;
    switch (key) {
        case SDLK_LEFT: return 37;
        case SDLK_UP: return 38;
        case SDLK_RIGHT: return 39;
        case SDLK_DOWN: return 40;
        case SDLK_DELETE: return 46;
        case SDLK_HOME: return 36;
        case SDLK_END: return 35;
        case SDLK_PAGEUP: return 33;
        case SDLK_PAGEDOWN: return 34;
        case SDLK_LSHIFT: case SDLK_RSHIFT: return 16;
        case SDLK_LCTRL: case SDLK_RCTRL: return 17;
        case SDLK_LALT: case SDLK_RALT: return 18;
        default: return key >= 0 && key <= 127 ? key : 0;
    }
}

struct Engine {
    engine_handle_t handle = nullptr;
    ~Engine() { if (handle) engine_destroy(handle); }
};

void option(Engine& engine, const char* key, const char* value) {
    engine_option_t setting{};
    setting.key_utf8 = key;
    setting.value_utf8 = value;
    check(engine_set_option(engine.handle, &setting), engine.handle);
}

void input(Engine& engine, const SDL_Event& raw, SDL_Window* window,
           uint32_t frame_width, uint32_t frame_height) {
    engine_input_event_t event{};
    event.struct_size = sizeof(event);
    event.timestamp_micros = std::chrono::duration_cast<std::chrono::microseconds>(
        Clock::now().time_since_epoch()).count();
    // The engine ABI uses TVP shift/alt/ctrl and mouse-button flags.
    auto modifiers = SDL_GetModState();
    event.modifiers = (modifiers & KMOD_SHIFT ? 1 : 0) |
                      (modifiers & KMOD_ALT ? 2 : 0) |
                      (modifiers & KMOD_CTRL ? 4 : 0);
    int width = 1, height = 1;
    SDL_GetWindowSize(window, &width, &height);
    int x = 0, y = 0;
    const auto buttons = SDL_GetMouseState(&x, &y);
    event.modifiers |= (buttons & SDL_BUTTON_LMASK ? 8 : 0) |
                       (buttons & SDL_BUTTON_RMASK ? 16 : 0) |
                       (buttons & SDL_BUTTON_MMASK ? 32 : 0);
    event.x = static_cast<double>(x) * frame_width / std::max(width, 1);
    event.y = static_cast<double>(y) * frame_height / std::max(height, 1);
    switch (raw.type) {
        case SDL_MOUSEBUTTONDOWN: case SDL_MOUSEBUTTONUP:
            event.type = raw.type == SDL_MOUSEBUTTONDOWN ? ENGINE_INPUT_EVENT_POINTER_DOWN
                                                        : ENGINE_INPUT_EVENT_POINTER_UP;
            event.button = raw.button.button == SDL_BUTTON_RIGHT ? 1 :
                           raw.button.button == SDL_BUTTON_MIDDLE ? 2 : 0;
            break;
        case SDL_MOUSEMOTION:
            event.type = ENGINE_INPUT_EVENT_POINTER_MOVE;
            break;
        case SDL_MOUSEWHEEL:
            event.type = ENGINE_INPUT_EVENT_POINTER_SCROLL;
            event.delta_x = raw.wheel.x;
            event.delta_y = raw.wheel.y;
            break;
        case SDL_KEYDOWN: case SDL_KEYUP:
            event.type = raw.type == SDL_KEYDOWN ? ENGINE_INPUT_EVENT_KEY_DOWN : ENGINE_INPUT_EVENT_KEY_UP;
            event.key_code = key_code(raw.key.keysym.sym);
            break;
        case SDL_TEXTINPUT: {
            // SDL emits committed IME text here, never intermediate composition.
            auto* converted = SDL_iconv_string("UTF-32LE", "UTF-8", raw.text.text,
                                               std::strlen(raw.text.text) + 1);
            if (!converted) return;
            auto* points = reinterpret_cast<uint32_t*>(converted);
            event.type = ENGINE_INPUT_EVENT_TEXT_INPUT;
            for (size_t i = 0; points[i]; ++i) {
                event.unicode_codepoint = points[i];
                check(engine_send_input(engine.handle, &event), engine.handle);
            }
            SDL_free(converted);
            return;
        }
        default: return;
    }
    check(engine_send_input(engine.handle, &event), engine.handle);
}

struct Host {
    Engine& engine;
    SDL_Window* window;
    SDL_Renderer* renderer;
    uint32_t width = 1280, height = 720;
    bool running = true;
    std::string error;
    std::vector<uint8_t> pixels;
    std::unique_ptr<SDL_Texture, decltype(&SDL_DestroyTexture)> texture{nullptr, SDL_DestroyTexture};

    Host(Engine& value, SDL_Window* w, SDL_Renderer* r) : engine(value), window(w), renderer(r) {}

    ~Host() { engine_set_host_callbacks(engine.handle, nullptr); }

    bool pump() {
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_QUIT) { running = false; break; }
            input(engine, event, window, width, height);
        }
        while (gtk_events_pending()) gtk_main_iteration();
        return running;
    }

    void present(const uint8_t* rgba, uint32_t frameWidth,
                 uint32_t frameHeight, uint32_t stride) {
        if (!frameWidth || !frameHeight || frameWidth > 3840 || frameHeight > 3840 ||
            stride < frameWidth * 4 || stride > 3840 * 4)
            throw std::runtime_error("Frame exceeds host bounds");
        if (!texture || frameWidth != width || frameHeight != height) {
            width = frameWidth; height = frameHeight;
            SDL_SetWindowSize(window, width, height);
            texture.reset(SDL_CreateTexture(renderer, SDL_PIXELFORMAT_RGBA32,
                                            SDL_TEXTUREACCESS_STREAMING, width, height));
            if (!texture) throw std::runtime_error(SDL_GetError());
        }
        if (SDL_UpdateTexture(texture.get(), nullptr, rgba, stride) != 0 ||
            SDL_RenderClear(renderer) != 0 || SDL_RenderCopy(renderer, texture.get(), nullptr, nullptr) != 0)
            throw std::runtime_error(SDL_GetError());
        SDL_RenderPresent(renderer);
    }

    static int32_t pumpCallback(void* context) {
        auto& host = *static_cast<Host*>(context);
        try { return host.pump() ? 1 : 0; }
        catch (const std::exception& exception) {
            host.error = exception.what(); host.running = false; return 0;
        }
    }

    static void presentCallback(void* context, const uint8_t* rgba, uint32_t width,
                                uint32_t height, uint32_t stride) {
        auto& host = *static_cast<Host*>(context);
        try { host.present(rgba, width, height, stride); }
        catch (const std::exception& exception) {
            host.error = exception.what(); host.running = false;
        }
    }
};
} // namespace

int main(int argc, char** argv) {
    if (argc != 3 || std::strcmp(argv[1], "--game") != 0) {
        std::fprintf(stderr, "Usage: vn-krkr-next --game PROJECT\n");
        return 2;
    }
    SDL_SetHint(SDL_HINT_VIDEODRIVER, "x11");
    // SDL software surfaces otherwise acquire a competing GL context on X11.
    SDL_SetHint(SDL_HINT_FRAMEBUFFER_ACCELERATION, "0");
    if (!gtk_init_check(nullptr, nullptr) || SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        std::fprintf(stderr, "Window initialization failed: %s\n", SDL_GetError());
        return 1;
    }
    int status = 0;
    try {
        std::unique_ptr<SDL_Window, decltype(&SDL_DestroyWindow)> window(
            SDL_CreateWindow("KrKr2-Next", SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED,
                             1280, 720, SDL_WINDOW_SHOWN), SDL_DestroyWindow);
        if (!window) throw std::runtime_error(SDL_GetError());
        // Software presentation avoids changing the engine's current EGL context.
        std::unique_ptr<SDL_Renderer, decltype(&SDL_DestroyRenderer)> renderer(
            SDL_CreateRenderer(window.get(), -1, SDL_RENDERER_SOFTWARE), SDL_DestroyRenderer);
        if (!renderer) throw std::runtime_error(SDL_GetError());
        Engine engine;
        std::string home = std::getenv("HOME") ? std::getenv("HOME") : ".";
        std::string cache = home + "/.cache";
        engine_create_desc_t description{};
        description.struct_size = sizeof(description);
        description.api_version = ENGINE_API_VERSION;
        description.writable_path_utf8 = home.c_str();
        description.cache_path_utf8 = cache.c_str();
        check(engine_create(&description, &engine.handle), engine.handle);
        std::fprintf(stderr, "KrKr2-Next Linux: ANGLE desktop GL, surfaceless\n");
        option(engine, ENGINE_OPTION_ANGLE_BACKEND, ENGINE_ANGLE_BACKEND_GLES);
        Host host{engine, window.get(), renderer.get()};
        engine_host_callbacks_t callbacks{};
        callbacks.struct_size = sizeof(callbacks);
        callbacks.pump_events = Host::pumpCallback;
        callbacks.present_frame = Host::presentCallback;
        callbacks.user_data = &host;
        check(static_cast<engine_result_t>(engine_set_host_callbacks(engine.handle, &callbacks)), engine.handle);
        SDL_StartTextInput();
        check(engine_open_game(engine.handle, argv[2], nullptr), engine.handle);
        auto previous = Clock::now();
        while (host.running) {
            if (!host.pump()) break;
            auto now = Clock::now();
            auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - previous).count();
            if (elapsed < 16) { SDL_Delay(1); continue; }
            previous = now;
            check(engine_tick(engine.handle, static_cast<uint32_t>(elapsed)), engine.handle);
            engine_frame_desc_t frame{};
            frame.struct_size = sizeof(frame);
            if (engine_get_frame_desc(engine.handle, &frame) != ENGINE_RESULT_OK || !frame.width) continue;
            host.pixels.resize(static_cast<size_t>(frame.stride_bytes) * frame.height);
            check(engine_read_frame_rgba(engine.handle, host.pixels.data(), host.pixels.size()), engine.handle);
            host.present(host.pixels.data(), frame.width, frame.height, frame.stride_bytes);
        }
        engine_set_host_callbacks(engine.handle, nullptr);
        if (!host.error.empty()) throw std::runtime_error(host.error);
        SDL_StopTextInput();
    } catch (const std::exception& error) {
        std::fprintf(stderr, "KrKr2-Next host failed: %s\n", error.what());
        status = 1;
    }
    SDL_Quit();
    return status;
}
