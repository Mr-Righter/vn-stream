cmake_minimum_required(VERSION 3.28)
if(NOT DEFINED KRKR_NEXT_SOURCE)
    message(FATAL_ERROR "Set KRKR_NEXT_SOURCE to the pinned Next checkout")
endif()
set(CMAKE_TOOLCHAIN_FILE "$ENV{VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake")
project(vn_stream_next_host LANGUAGES C CXX)
set(LINUX ON CACHE BOOL "Linux platform" FORCE)
add_compile_definitions(LINUX)
set(KRKR2_WITH_LIVE2D OFF CACHE BOOL "Unavailable SDK is not replaced by stub plugins" FORCE)
set(BUILD_ENGINE_API ON CACHE BOOL "Real engine ABI" FORCE)
set(ENABLE_TESTS OFF CACHE BOOL "Upstream test programs" FORCE)
set(BUILD_TOOLS OFF CACHE BOOL "Upstream tools" FORCE)
# The old standalone Linux executable references removed Cocos UI. Build the
# actual core/bridge targets and this host, leaving that executable excluded.
add_subdirectory("${KRKR_NEXT_SOURCE}" upstream EXCLUDE_FROM_ALL)
find_package(PkgConfig REQUIRED)
pkg_check_modules(GTK3 REQUIRED IMPORTED_TARGET gtk+-3.0)
find_package(SDL2 CONFIG REQUIRED)
target_link_libraries(core_environ_module PRIVATE PkgConfig::GTK3)
add_executable(vn-krkr-next main.cpp)
target_compile_features(vn-krkr-next PRIVATE cxx_std_17)
target_link_libraries(vn-krkr-next PRIVATE engine_api SDL2::SDL2 PkgConfig::GTK3)
set_target_properties(vn-krkr-next PROPERTIES BUILD_RPATH "$ORIGIN/lib" INSTALL_RPATH "$ORIGIN/lib")
# Legacy FFmpeg x86 assembly uses PC-relative internal constants. Keep those
# archive symbols local when linking the real ABI shared library.
target_link_options(engine_api PRIVATE
    "LINKER:--exclude-libs,libavcodec.a:libavformat.a:libavutil.a:libavfilter.a:libswresample.a:libswscale.a")
