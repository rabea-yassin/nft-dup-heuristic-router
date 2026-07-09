cmake_minimum_required(VERSION 3.16)
project(nft_dup_heuristic_router C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

add_compile_options(-Wall -Wextra)

# Vendored single-header PNG/image decoder; implementation lives in its own
# translation unit so every other .c file can just #include the header.
add_library(stb_image STATIC third_party/stb_image_impl.c)
target_include_directories(stb_image PUBLIC third_party)

find_library(MATH_LIBRARY m)
if(MATH_LIBRARY)
  target_link_libraries(stb_image PUBLIC ${MATH_LIBRARY})
endif()

# Each hash lives in its own src/hashes/<name>/ directory (see the READMEs
# there); shared helpers live in src/hashes/common/. Every directory is on
# the include path so headers are referenced by bare name regardless of
# which hash a translation unit belongs to (e.g. shash includes dhash).
add_library(hashes STATIC
  src/hashes/common/hashes_common.c
  src/hashes/common/pil_ops.c
  src/hashes/ahash/ahash.c
  src/hashes/phash/phash.c
  src/hashes/hsvhash/hsvhash.c
  src/hashes/dhash/dhash.c
  src/hashes/shash/shash.c
)
target_include_directories(hashes PUBLIC
  src/hashes/common
  src/hashes/ahash
  src/hashes/phash
  src/hashes/hsvhash
  src/hashes/dhash
  src/hashes/shash
)
target_link_libraries(hashes PUBLIC stb_image)

add_executable(ahash_sanity_check src/hashes/ahash/ahash_sanity_check.c)
target_link_libraries(ahash_sanity_check PRIVATE hashes)

add_executable(phash_sanity_check src/hashes/phash/phash_sanity_check.c)
target_link_libraries(phash_sanity_check PRIVATE hashes)

add_executable(hsvhash_sanity_check src/hashes/hsvhash/hsvhash_sanity_check.c)
target_link_libraries(hsvhash_sanity_check PRIVATE hashes)

add_executable(dhash_sanity_check src/hashes/dhash/dhash_sanity_check.c)
target_link_libraries(dhash_sanity_check PRIVATE hashes)

add_executable(shash_sanity_check src/hashes/shash/shash_sanity_check.c)
target_link_libraries(shash_sanity_check PRIVATE hashes)
