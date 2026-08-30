cmake_minimum_required(VERSION 3.24)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" LOCALCODEX_VERSION)
string(STRIP "${LOCALCODEX_VERSION}" LOCALCODEX_VERSION)
project(LocalCodex VERSION "${LOCALCODEX_VERSION}" LANGUAGES C CXX)

include(CTest)

option(LOCALCODEX_BUILD_MONITOR "Build the native LocalCodex monitor" ON)
option(LOCALCODEX_BUILD_TESTS "Build native monitor tests" ON)

if(LOCALCODEX_BUILD_MONITOR)
  add_subdirectory(monitor)
endif()
