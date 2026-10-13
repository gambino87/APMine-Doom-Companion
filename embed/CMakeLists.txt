# APMine companion change, 2026-10-11. See COPYING.md and APCpp/LICENSE.
# Resource archives are reconstructed on the player's computer, never compiled in.
file(GLOB disallowed_embeds CONFIGURE_DEPENDS "*.zip" "*.apworld")
if(disallowed_embeds)
    message(FATAL_ERROR "Companion builds must not embed asset or world archives: ${disallowed_embeds}")
endif()

set(embed_root "${CMAKE_CURRENT_BINARY_DIR}/..")
file(WRITE "${embed_root}/embedded_files.h" "
#ifndef EMBEDDED_FILES_H
#define EMBEDDED_FILES_H
#include <stddef.h>
#ifdef __cplusplus
extern \"C\" {
#endif
typedef struct { const char *name; const size_t size; const char *data; } embedded_file_t;
extern const embedded_file_t embedded_files[1];
#define NUM_EMBEDDED_FILES 0
#ifdef __cplusplus
}
#endif
#endif
")
file(WRITE "${embed_root}/embedded_files.c" "
#include \"embedded_files.h\"
const embedded_file_t embedded_files[1] = {{NULL, 0, NULL}};
")
add_library(embeds STATIC "${embed_root}/embedded_files.c" "${embed_root}/embedded_files.h")
