cmake_minimum_required(VERSION 3.10)
project(modulatore VERSION 0.2.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_compile_options(-O3 -march=native -g)

option(USE_LIBIIO "Enable PlutoSDR output via libiio" ON)

include_directories(${CMAKE_SOURCE_DIR}/src ${CMAKE_SOURCE_DIR}/librds)

set(SOURCES
    src/main.cpp
    src/audio_pipeline.cpp
    src/audio_input.cpp
    src/control_udp.cpp
    src/pluto_output.cpp
    librds/rds.c
    librds/rds_strings.c
)

add_executable(modulatore ${SOURCES})
target_link_libraries(modulatore m pthread)

if(USE_LIBIIO)
    find_library(LIBIIO_LIBRARY iio)
    if(LIBIIO_LIBRARY)
        target_compile_definitions(modulatore PRIVATE USE_LIBIIO)
        target_link_libraries(modulatore ${LIBIIO_LIBRARY})
        if(EXISTS ${CMAKE_SOURCE_DIR}/tools/pluto_probe.c)
            add_executable(pluto_probe tools/pluto_probe.c)
            target_link_libraries(pluto_probe ${LIBIIO_LIBRARY})
        endif()
    else()
        message(WARNING "libiio non trovata: PlutoSDR disabilitato, si userà stdout.")
    endif()
endif()
