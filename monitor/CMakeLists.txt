include(FetchContent)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(SDL_SHARED OFF CACHE BOOL "" FORCE)
set(SDL_STATIC ON CACHE BOOL "" FORCE)
set(SDL_TEST_LIBRARY OFF CACHE BOOL "" FORCE)
set(SDL_TESTS OFF CACHE BOOL "" FORCE)
set(SDL_EXAMPLES OFF CACHE BOOL "" FORCE)
set(HTTPLIB_INSTALL OFF CACHE BOOL "" FORCE)
if(MINGW)
  # MinGW headers lack GetAddrInfoExCancel. The monitor accepts numeric loopback
  # addresses only, so synchronous address parsing does not need a DNS timeout.
  set(HTTPLIB_USE_NON_BLOCKING_GETADDRINFO OFF CACHE BOOL "" FORCE)
endif()

FetchContent_Declare(SDL
  URL https://github.com/libsdl-org/SDL/archive/refs/tags/release-3.4.14.tar.gz
  URL_HASH SHA256=9d57b178fb297e121ef2605275937b7afaa7cd24d99ce1f95953e69e7a2535d6)
FetchContent_Declare(imgui
  URL https://github.com/ocornut/imgui/archive/refs/tags/v1.92.9.tar.gz
  URL_HASH SHA256=af97ed649182c39314320514a672b82008ab462b9293fe23d37b30bfa5d05519)
FetchContent_Declare(implot
  URL https://github.com/epezent/implot/archive/7eeb9168d2e5e6b14e266d8782ecf7e649dfc3a4.tar.gz
  URL_HASH SHA256=501243b6af4796c6a89153bb8bd5282f10997a69322f42f92717aaa06e4fc341)
FetchContent_Declare(httplib
  URL https://github.com/yhirose/cpp-httplib/archive/refs/tags/v0.54.1.tar.gz
  URL_HASH SHA256=7310f5312e1423830d649b38ed028e9db86303a979ccbfdbd1c4b1574f422dfb)
FetchContent_Declare(json
  URL https://github.com/nlohmann/json/archive/refs/tags/v3.12.0.tar.gz
  URL_HASH SHA256=4b92eb0c06d10683f7447ce9406cb97cd4b453be18d7279320f7b2f025c10187)

FetchContent_MakeAvailable(SDL imgui implot httplib json)
find_package(OpenGL REQUIRED)

add_library(localcodex_imgui STATIC
  ${imgui_SOURCE_DIR}/imgui.cpp
  ${imgui_SOURCE_DIR}/imgui_draw.cpp
  ${imgui_SOURCE_DIR}/imgui_tables.cpp
  ${imgui_SOURCE_DIR}/imgui_widgets.cpp
  ${imgui_SOURCE_DIR}/backends/imgui_impl_sdl3.cpp
  ${imgui_SOURCE_DIR}/backends/imgui_impl_opengl3.cpp
  ${implot_SOURCE_DIR}/implot.cpp
  ${implot_SOURCE_DIR}/implot_items.cpp)
target_include_directories(localcodex_imgui PUBLIC
  ${imgui_SOURCE_DIR}
  ${imgui_SOURCE_DIR}/backends
  ${implot_SOURCE_DIR})
target_link_libraries(localcodex_imgui PUBLIC SDL3::SDL3-static OpenGL::GL)

add_library(localcodex_monitor_core STATIC
  src/monitor_state.cpp
  src/system_metrics.cpp)
target_include_directories(localcodex_monitor_core PUBLIC src)
target_link_libraries(localcodex_monitor_core PUBLIC nlohmann_json::nlohmann_json)
if(WIN32)
  target_link_libraries(localcodex_monitor_core PUBLIC dxgi pdh)
elseif(CMAKE_DL_LIBS)
  target_link_libraries(localcodex_monitor_core PUBLIC ${CMAKE_DL_LIBS})
endif()

add_executable(localcodex-monitor WIN32 src/main.cpp)
target_sources(localcodex-monitor PRIVATE src/i18n.cpp src/monitor_client.cpp)
target_compile_definitions(localcodex-monitor PRIVATE
  CPPHTTPLIB_OPEN_TIMEOUT_SECOND=1
  CPPHTTPLIB_READ_TIMEOUT_SECOND=2
  LOCALCODEX_VERSION="${PROJECT_VERSION}")
target_link_libraries(localcodex-monitor PRIVATE
  localcodex_imgui localcodex_monitor_core httplib::httplib nlohmann_json::nlohmann_json)

if(MSVC)
  target_compile_options(localcodex-monitor PRIVATE /W4 /permissive-)
else()
  target_compile_options(localcodex-monitor PRIVATE -Wall -Wextra -Wpedantic)
endif()

install(TARGETS localcodex-monitor RUNTIME DESTINATION .)
install(FILES ${PROJECT_SOURCE_DIR}/THIRD_PARTY_NOTICES.md DESTINATION .)
install(FILES ${PROJECT_SOURCE_DIR}/LICENSE DESTINATION .)
install(FILES ${imgui_SOURCE_DIR}/LICENSE.txt DESTINATION licenses RENAME LICENSE.DearImGui.txt)
install(FILES ${implot_SOURCE_DIR}/LICENSE DESTINATION licenses RENAME LICENSE.ImPlot.txt)
install(FILES ${sdl_SOURCE_DIR}/LICENSE.txt DESTINATION licenses RENAME LICENSE.SDL3.txt)
install(FILES ${httplib_SOURCE_DIR}/LICENSE DESTINATION licenses RENAME LICENSE.cpp-httplib.txt)
install(FILES ${json_SOURCE_DIR}/LICENSE.MIT DESTINATION licenses RENAME LICENSE.nlohmann-json.txt)

if(LOCALCODEX_BUILD_TESTS)
  enable_testing()
  add_executable(localcodex-monitor-tests tests/monitor_state_tests.cpp)
  target_link_libraries(localcodex-monitor-tests PRIVATE localcodex_monitor_core)
  add_test(NAME localcodex-monitor-state COMMAND localcodex-monitor-tests)
endif()
