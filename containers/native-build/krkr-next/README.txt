KrKr2-Next: VN Stream Linux window host

Source: reAAAq/KrKr2-Next, 1abd1ed4e8aec7abd5d2524c3a9ad7886880602f
Host: SDL2/X11, real engine_api ABI, ANGLE desktop OpenGL on surfaceless Mesa.
The private lib/libengine_api.so must accompany runner. No Cocos Resources
folder or Flutter frontend is required. Use --game PROJECT for loose startup.tjs,
or --game PROJECT/data.xp3 for a packed entry. HOME must be project-specific.

This is a manual compatibility candidate. krkr-rs remains the default.
Not all game-specific plugins are supported. Live2D and krkrgles are disabled
because the SDK is unavailable; they are not replaced by fake implementations.
AlphaMovie and kirikiroid2 text helpers are real implementations imported from
Project-Shinkirou/kirikiroid2-web f7c351ac3f4b82eb70b18d96e3c103f49f019b88.
See LICENSE-components.txt and the included source.patch for attribution.
Dependency notices are in THIRD_PARTY-NOTICES.txt. Build inputs, dependency
baseline and patch order are in build.json. See docs/verification in VN Stream
for the exact tested scope; startup is not a full game compatibility guarantee.

Modal dialogs run the host event and presentation callbacks on the engine
thread. Child-window close does not terminate the main project. The host clears
callbacks before destroying the engine, and engine teardown stops workers before
shared-library static destruction. Software SDL presentation intentionally avoids
acquiring a second OpenGL context. Do not enable SDL framebuffer acceleration.
