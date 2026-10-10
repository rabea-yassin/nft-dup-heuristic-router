/* Timing harness for the C sHash -- the "like for like" half of the ORB vs
 * sHash time comparison (python/geometric/bench_time.py drives it and does
 * the ORB side).
 *
 * Both sides are timed from decoded pixels: every image is decoded first
 * and the PNG decode is kept out of the timed region, because it is shared
 * work (both signals need the pixels) and the two sides would decode with
 * different libraries. What is timed is exactly the work that differs:
 *
 *   image  shash_from_rgb: RGB->L, 300x300 resize, blur, median, segment,
 *          crop + dhash each segment (the "fingerprint a new image" task)
 *   pair   shash_paper_distance (the "compare one pair" task). It takes
 *          tens of nanoseconds, below what one clock read resolves, so each
 *          sample times a batch of calls and divides.
 *
 * Every item gets one warm-up run, then `repeats` timed runs, and the
 * per-item median is written out. Single-threaded.
 *
 * Usage:
 *   bench_shash <images_dir> <images.txt> <pairs.txt> <out_images.csv>
 *               <out_pairs.csv> [repeats]
 * images.txt: one filename per line. pairs.txt: "original,copy" per line,
 * both names also listed in images.txt. The original is the source side
 * of the (directional) paper distance, as in shash_baseline.py. */
#define _POSIX_C_SOURCE 200809L

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "shash.h"
#include "stb_image.h"

#define NAME_MAX_LEN 512
#define PAIR_BATCH 2000

typedef struct {
    char name[NAME_MAX_LEN];
    shash_t hash;
} entry;

static double now_ns(void) {
    struct timespec ts;
#ifdef CLOCK_MONOTONIC_RAW
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
#else
    clock_gettime(CLOCK_MONOTONIC, &ts);
#endif
    return (double)ts.tv_sec * 1e9 + (double)ts.tv_nsec;
}

static int cmp_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static double median(double *v, int n) {
    qsort(v, (size_t)n, sizeof(double), cmp_double);
    return n % 2 ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2.0;
}

static int cmp_entry(const void *a, const void *b) {
    return strcmp(((const entry *)a)->name, ((const entry *)b)->name);
}

static void chomp(char *s) {
    size_t n = strlen(s);
    while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ')) s[--n] = '\0';
}

int main(int argc, char **argv) {
    if (argc < 6) {
        fprintf(stderr, "usage: %s <images_dir> <images.txt> <pairs.txt> "
                        "<out_images.csv> <out_pairs.csv> [repeats]\n", argv[0]);
        return EXIT_FAILURE;
    }
    const char *dir = argv[1];
    int repeats = argc > 6 ? atoi(argv[6]) : 5;
    if (repeats < 1) repeats = 1;

    FILE *list = fopen(argv[2], "r");
    FILE *out_img = fopen(argv[4], "w");
    if (!list || !out_img) {
        fprintf(stderr, "cannot open image list or output\n");
        return EXIT_FAILURE;
    }

    size_t cap = 1024, n = 0;
    entry *entries = malloc(cap * sizeof(entry));
    double *samples = malloc((size_t)repeats * sizeof(double));
    char line[2 * NAME_MAX_LEN], path[2 * NAME_MAX_LEN];

    fprintf(out_img, "image,width,height,segments,decode_ns,hash_ns\n");
    while (fgets(line, sizeof line, list)) {
        chomp(line);
        if (!line[0]) continue;
        if (n == cap) entries = realloc(entries, (cap *= 2) * sizeof(entry));
        entry *e = &entries[n];
        snprintf(e->name, NAME_MAX_LEN, "%s", line);
        snprintf(path, sizeof path, "%s/%s", dir, line);

        int w, h, ch;
        double t0 = now_ns();
        unsigned char *px = stbi_load(path, &w, &h, &ch, 3);
        double decode_ns = now_ns() - t0;
        if (!px) {
            fprintf(stderr, "cannot decode %s\n", path);
            return EXIT_FAILURE;
        }

        if (shash_from_rgb(px, w, h, &e->hash) != 0) { /* warm-up */
            fprintf(stderr, "cannot hash %s\n", path);
            return EXIT_FAILURE;
        }
        for (int r = 0; r < repeats; r++) {
            t0 = now_ns();
            shash_from_rgb(px, w, h, &e->hash);
            samples[r] = now_ns() - t0;
        }
        stbi_image_free(px);

        fprintf(out_img, "%s,%d,%d,", e->name, w, h);
        for (int s = 0; s < e->hash.count; s++) {
            fprintf(out_img, "%s%016llx", s ? "|" : "",
                    (unsigned long long)e->hash.segment_hashes[s]);
        }
        fprintf(out_img, ",%.0f,%.0f\n", decode_ns, median(samples, repeats));
        n++;
    }
    fclose(list);
    fclose(out_img);
    qsort(entries, n, sizeof(entry), cmp_entry);

    FILE *pairs = fopen(argv[3], "r");
    FILE *out_pair = fopen(argv[5], "w");
    if (!pairs || !out_pair) {
        fprintf(stderr, "cannot open pair list or output\n");
        return EXIT_FAILURE;
    }
    fprintf(out_pair, "original_image,copy_image,shash_dist,compare_ns\n");
    volatile double sink = 0.0;
    while (fgets(line, sizeof line, pairs)) {
        chomp(line);
        char *comma = strchr(line, ',');
        if (!comma) continue;
        *comma = '\0';
        entry key_a, key_b;
        snprintf(key_a.name, NAME_MAX_LEN, "%s", line);
        snprintf(key_b.name, NAME_MAX_LEN, "%s", comma + 1);
        entry *a = bsearch(&key_a, entries, n, sizeof(entry), cmp_entry);
        entry *b = bsearch(&key_b, entries, n, sizeof(entry), cmp_entry);
        if (!a || !b) {
            fprintf(stderr, "pair image not in list: %s,%s\n", key_a.name, key_b.name);
            return EXIT_FAILURE;
        }

        double dist = shash_paper_distance(&a->hash, &b->hash); /* warm-up */
        for (int r = 0; r < repeats; r++) {
            double t0 = now_ns();
            for (int k = 0; k < PAIR_BATCH; k++) {
                sink += shash_paper_distance(&a->hash, &b->hash);
            }
            samples[r] = (now_ns() - t0) / PAIR_BATCH;
        }
        fprintf(out_pair, "%s,%s,%.4f,%.1f\n", a->name, b->name, dist,
                median(samples, repeats));
    }
    fclose(pairs);
    fclose(out_pair);
    free(entries);
    free(samples);
    return sink < 0 ? EXIT_FAILURE : EXIT_SUCCESS; /* keeps `sink` live */
}
