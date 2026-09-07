# Rebuilding release sources

Each native monitor release includes a corresponding `localcodex-source-PLATFORM-vVERSION.tar.gz`
asset next to the binary, checksums and core archive. It contains `localcodex/` (tracked
project sources) and `third_party/` (the actual pinned CMake dependency sources used by CI).
The dependency directories retain their own licenses; original project code is AGPL-3.0-only.

Extract the source archive, enter `localcodex/`, install the platform build prerequisites
listed in the CI workflow, and configure CMake with these additional flags to use bundled
sources without fetching native dependencies:

```sh
cmake -S . -B build -DLOCALCODEX_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release \
  -DFETCHCONTENT_SOURCE_DIR_SDL="$PWD/../third_party/sdl-src" \
  -DFETCHCONTENT_SOURCE_DIR_IMGUI="$PWD/../third_party/imgui-src" \
  -DFETCHCONTENT_SOURCE_DIR_IMPLOT="$PWD/../third_party/implot-src" \
  -DFETCHCONTENT_SOURCE_DIR_HTTPLIB="$PWD/../third_party/httplib-src" \
  -DFETCHCONTENT_SOURCE_DIR_JSON="$PWD/../third_party/json-src"
cmake --build build --config Release --parallel
ctest --test-dir build -C Release --output-on-failure
```

On Windows use the equivalent absolute paths and your installed Visual Studio generator.
On Linux, `cmake/mingw-w64.cmake` provides an optional MinGW cross-build; that does not
replace running the resulting binary/tests on Windows. Python dependencies are separately
specified by requirements-local-codex.txt and constraints-runtime.txt. Models, Codex CLI,
Ollama and OS toolchains are external prerequisites and not included in release assets.
