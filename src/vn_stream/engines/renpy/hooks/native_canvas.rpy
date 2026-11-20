# Service-owned Python 2/3-compatible searchpath script. Original game/SDK untouched.
init 999 python:
    def _stream_native_canvas_init():
        import os
        import json
        import time
        import renpy
        report = os.environ.get("VN_STREAM_CANVAS_REPORT")
        session = os.environ.get("VN_STREAM_SESSION_ID")
        if not report or not session:
            return
        ack = None
        try:
            data = {"session_id": session, "width": config.screen_width, "height": config.screen_height}
            temporary = report + ".tmp"
            with open(temporary, "w") as output:
                json.dump(data, output)
            os.rename(temporary, report)
            # Each server display operation is bounded. Slow game initialization
            # happens before this hook and is never killed to meet the 5s deadline.
            deadline = time.time() + 5.5
            while time.time() < deadline:
                try:
                    with open(os.environ["VN_STREAM_CANVAS_ACK"]) as source:
                        ack = json.load(source)
                    if ack.get("session_id") == session and ack.get("diagnostic") != "report_timeout":
                        break
                except (IOError, ValueError):
                    pass
                time.sleep(0.02)
        except Exception:
            # A failed report must not prevent a game from starting.
            pass
        # Fullscreen supplies the real display dimensions; adjust_view_size keeps
        # small/odd canvases at 1:1 and leaves alignment padding black. Ren'Py's
        # physical_box transforms rendering and pointer input together.
        def _stream_native_view(width, height):
            import math
            vw, vh = config.screen_width, config.screen_height
            scale = min(1.0, 3840.0 / max(vw, vh), 2160.0 / min(vw, vh),
                        float(width) / vw, float(height) / vh)
            return max(1, int(math.floor(vw * scale))), max(1, int(math.floor(vh * scale)))
        config.adjust_view_size = _stream_native_view
        native_content = _stream_native_view(config.screen_width, config.screen_height)
        padded = native_content[0] % 8 != 0 or native_content[1] % 2 != 0
        # Older SDKs (7.5/8.0) lack the pre-interaction display-start hook;
        # their verified fullscreen renderer handles aligned padding directly.
        try:
            native_ready = (ack.get("state") == "native" and len(ack["capture"]) == 2
                            and ack["capture"][0] >= native_content[0]
                            and ack["capture"][1] >= native_content[1])
        except Exception:
            native_ready = False
        content_window = padded and native_ready and hasattr(renpy.config, "display_start_callbacks")
        _preferences.fullscreen = not content_window
        if content_window:
            # Keep old GL2 screenshot paths away from half-pixel/odd subviews.
            # An exact undecorated content window leaves integer black padding
            # outside the window, on the server-owned capture display.
            import pygame_sdl2 as pygame
            pygame.display.init()
            if hasattr(renpy.game.preferences, "window_position_layout"):
                renpy.game.preferences.window_position = ((ack["capture"][0] - native_content[0]) // 2, (ack["capture"][1] - native_content[1]) // 2)
                renpy.game.preferences.window_position_layout = tuple(
                    pygame.display.get_display_bounds(i)
                    for i in range(pygame.display.get_num_video_displays()))
            config.gl_resize = False
            old_flags = config.gl2_modify_window_flags
            def _stream_window_flags(flags):
                import pygame_sdl2 as pygame
                return (old_flags(flags) if old_flags is not None else flags) | pygame.NOFRAME
            config.gl2_modify_window_flags = _stream_window_flags
            _preferences.physical_size = native_content
            placed = [False]
            def _stream_place_window():
                if placed[0]:
                    return
                try:
                    import pygame_sdl2 as pygame
                    window = pygame.display.get_window()
                    if tuple(renpy.exports.get_physical_size()) != native_content:
                        window.resize(native_content, opengl=True, fullscreen=False)
                    capture = renpy.display.get_info()
                    window.set_position((max(0, (capture.current_w - native_content[0]) // 2),
                                         max(0, (capture.current_h - native_content[1]) // 2)))
                    placed[0] = True
                except Exception:
                    pass
            if hasattr(renpy.config, "display_start_callbacks"):
                config.display_start_callbacks.append(_stream_place_window)
            config.interact_callbacks.append(_stream_place_window)
        last_render = [None]
        def _stream_native_render():
            try:
                physical = list(renpy.exports.get_physical_size())
                viewport = list(renpy.display.draw.physical_box)
                import pygame_sdl2 as pygame
                position = list(pygame.display.get_position()) if content_window else [0, 0]
                value = {"session_id": session, "physical": physical, "viewport": viewport,
                         "position": position}
                if value == last_render[0]:
                    return
                target = os.path.join(os.path.dirname(report), "render.json")
                with open(target + ".tmp", "w") as output:
                    json.dump(value, output)
                os.rename(target + ".tmp", target)
                last_render[0] = value
            except Exception:
                pass
        config.periodic_callbacks.append(_stream_native_render)

    renpy.game.post_init.append(_stream_native_canvas_init)
