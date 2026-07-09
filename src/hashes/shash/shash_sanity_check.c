/* Build-and-eyeball check, not a formal test suite (that's Roadmap step 5,
 * with imagehash as the parity oracle). sHash is shaped differently from
 * the other three: its result is a LIST of per-segment dhashes, and
 * comparison is a best-match pairing, not one Hamming distance. So this
 * reports two numbers per pair -- the paper's own sHash distance
 * (mean-of-mins, the metric its Table II / thresholds use) and imagehash's
 * ImageMultiHash.__sub__ score -- against the same committed
 * data/example/generated/ fixtures the other sanity checks use.
 *
 * Expectation: an exact copy gives paper-distance 0 and __sub__ 0; the
 * color/background variants give clearly nonzero paper distances. (Unlike
 * dhash on the whole image, sHash's per-segment dhashes are computed on
 * bounding-box crops, so an unrelated image is what drives the distance
 * up -- see the last comparison.) */
#include <stdio.h>
#include <stdlib.h>

#include "shash.h"

static int hash_and_report(const char *path, shash_t *out_hash) {
    int rc = shash_from_file(path, out_hash);
    if (rc != 0) {
        fprintf(stderr, "failed to hash %s (rc=%d)\n", path, rc);
        return rc;
    }
    printf("%-70s -> %d segment(s):", path, out_hash->count);
    for (int i = 0; i < out_hash->count; i++) {
        printf(" %016llx", (unsigned long long)out_hash->segment_hashes[i]);
    }
    printf("\n");
    return 0;
}

int main(int argc, char **argv) {
    const char *dir = (argc > 1) ? argv[1] : "data/example/generated/images";
    char path[1024];
    shash_t original, exact_copy, color_swap, background_change, other;

    snprintf(path, sizeof(path), "%s/azuki_#824.png", dir);
    if (hash_and_report(path, &original) != 0) return EXIT_FAILURE;

    snprintf(path, sizeof(path), "%s/azuki_#824__exact_copy__0.png", dir);
    if (hash_and_report(path, &exact_copy) != 0) return EXIT_FAILURE;

    snprintf(path, sizeof(path), "%s/azuki_#824__color_swap_modify_saturate__0.png", dir);
    if (hash_and_report(path, &color_swap) != 0) return EXIT_FAILURE;

    snprintf(path, sizeof(path), "%s/azuki_#824__background_color_change__0.png", dir);
    if (hash_and_report(path, &background_change) != 0) return EXIT_FAILURE;

    /* An unrelated image (different collection) as a true non-duplicate. */
    snprintf(path, sizeof(path), "%s/bayc_#1242.png", dir);
    if (hash_and_report(path, &other) != 0) return EXIT_FAILURE;

    printf("\n%-34s paper_dist   __sub__\n", "comparison (vs original)");
    printf("%-34s %9.3f  %8.3f  (expect 0 / 0)\n", "exact_copy",
           shash_paper_distance(&original, &exact_copy), shash_sub(&original, &exact_copy));
    printf("%-34s %9.3f  %8.3f\n", "color_swap_saturate",
           shash_paper_distance(&original, &color_swap), shash_sub(&original, &color_swap));
    printf("%-34s %9.3f  %8.3f\n", "background_color_change",
           shash_paper_distance(&original, &background_change), shash_sub(&original, &background_change));
    printf("%-34s %9.3f  %8.3f  (expect clearly nonzero)\n", "other image (bayc_#1242)",
           shash_paper_distance(&original, &other), shash_sub(&original, &other));

    double d_exact = shash_paper_distance(&original, &exact_copy);
    if (d_exact != 0.0) {
        fprintf(stderr, "\nFAIL: exact copy did not give paper-distance 0 (got %.3f)\n", d_exact);
        return EXIT_FAILURE;
    }
    if (!shash_matches(&original, &exact_copy)) {
        fprintf(stderr, "\nFAIL: exact copy did not register as a match\n");
        return EXIT_FAILURE;
    }

    printf("\nOK\n");
    return EXIT_SUCCESS;
}
