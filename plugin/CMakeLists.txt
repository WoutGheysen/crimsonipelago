cmake_minimum_required(VERSION 3.20)
project(crimsonipelago_plugin CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CRIMSONIPELAGO_PLUGIN_VERSION "0.8.0-r3" CACHE STRING "plugin version reported to the client")

add_library(crimsonipelago SHARED src/main.cpp)
target_compile_definitions(crimsonipelago PRIVATE CRIMSONIPELAGO_PLUGIN_VERSION="${CRIMSONIPELAGO_PLUGIN_VERSION}"
                           _CRT_SECURE_NO_WARNINGS UNICODE _UNICODE)
target_link_libraries(crimsonipelago PRIVATE ws2_32 psapi version)

# ASI loaders load *.asi files: same DLL, different extension.
set_target_properties(crimsonipelago PROPERTIES PREFIX "" SUFFIX ".asi" OUTPUT_NAME "crimsonipelago")

if(MSVC)
  target_compile_options(crimsonipelago PRIVATE /W4 /EHsc /permissive-)
  # static runtime: nothing extra to install next to the game
  set_property(TARGET crimsonipelago PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")
else()
  target_compile_options(crimsonipelago PRIVATE -Wall -Wextra)
  target_link_options(crimsonipelago PRIVATE -static -static-libgcc -static-libstdc++)
endif()

option(CRIMSONIPELAGO_TEST_HOST "build the CI test host (an executable named CrimsonDesert.exe)" OFF)
if(CRIMSONIPELAGO_TEST_HOST)
  add_executable(test_host test/host.cpp)
  set_target_properties(test_host PROPERTIES OUTPUT_NAME "CrimsonDesert")
  if(MSVC)
    set_property(TARGET test_host PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")
  else()
    target_link_options(test_host PRIVATE -municode -static)
  endif()
endif()
