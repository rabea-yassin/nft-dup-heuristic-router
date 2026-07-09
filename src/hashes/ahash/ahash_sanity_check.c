/* Build-and-eyeball check, not a formal test suite (that's Roadmap step 5,
 * with imagehash as the parity oracle). Hashes a fixed pair of images from
 * the committed data/example/generated/ fixtures and prints the resulting
 * Hamming distance: 0 is expected for an exact copy, clearly nonzero for a
 * visibly different variant. */
#include <stdio.h>
#include <stdlib.h>

#include "ahash.h"

static int hash_and_report(const char *path, ahash_t *out_hash) {
    int rc = ahash_from_file(path, out_hash);
    if (rc != 0) {
        fprintf(stderr, "failed to hash %s (rc=%d)\n", path, rc);
        return rc;
    }
    printf("%-70s -> %016llx\n", path, (unsigned long long)*out_hash);
    return 0;
}

int main(int argc, char **argv) {
    const char *dir = (argc > 1) ? argv[1] : "data/example/generated/images";
    char path[1024];
    ahash_t original, exact_copy, color_swap, background_change;

    snprintf(path, sizeof(path), "%s/azuki_#824.png", dir);
    if (hash_and_report(path, &original) != 0) return EXIT_FAILURE;

    snprintf(path, sizeof(path), "%s/azuki_#824__exact_copy__0.png", dir);
    if (hash_and_report(path, &exact_copy) != 0) return EXIT_FAILURE;

    snprintf(path, sizeof(path), "%s/azuki_#824__color_swap_modify_saturate__0.png", dir);
    if (hash_and_report(path, &color_swap) != 0) return EXIT_FAILURE;

    snprintf(path, sizeof(path), "%s/azuki_#824__background_color_change__0.png", dir);
    if (hash_and_report(path, &background_change) != 0) return EXIT_FAILURE;

    int d_exact = ahash_distance(original, exact_copy);
    int d_color = ahash_distance(original, color_swap);
    int d_background = ahash_distance(original, background_change);

    printf("\noriginal vs exact_copy:          distance = %d (expect 0)\n", d_exact);
    printf("original vs color_swap_saturate: distance = %d (expect clearly nonzero)\n", d_color);
    printf("original vs background_change:   distance = %d (expect clearly nonzero)\n", d_background);

    if (d_exact != 0) {
        fprintf(stderr, "\nFAIL: exact copy did not hash to distance 0\n");
        return EXIT_FAILURE;
    }
    if (d_color == 0 && d_background == 0) {
        fprintf(stderr, "\nFAIL: both variants hashed identically to the original\n");
        return EXIT_FAILURE;
    }

    printf("\nOK\n");
    return EXIT_SUCCESS;
}
