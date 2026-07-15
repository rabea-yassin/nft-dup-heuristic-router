# NFT Duplicate Detection Pipeline

## Overview
This repository contains a heuristic routing and computer vision pipeline engineered to detect heavily manipulated NFT duplicates. The core objective of this architecture is to robustly identify duplicate images that have been subjected to severe geometric transformations, specifically targeting **cropped** and **rotated** variations, alongside mirrored copies.

To achieve this at scale without relying on brute-force comparisons, the system utilizes a three-stage computer vision pipeline: ORB feature extraction, Locality Sensitive Hashing (LSH) for rapid candidate retrieval, and RANSAC for strict geometric verification.

---

## The Core Pipeline

### Stage 1: Feature Extraction (ORB)
Before any searching occurs, the system must translate images into mathematical representations. We utilize ORB (Oriented FAST and Rotated BRIEF) due to its rotational invariance and high performance.
*   The system scans an image and identifies distinct visual anchor points (keypoints) like sharp corners or high-contrast edges.
*   It extracts exactly 1,000 binary descriptors for these keypoints.
*   For the baseline gallery (`data/raw/`), all extracted descriptors are flattened into a single, massive global matrix and saved offline.

### Stage 2: Rapid Candidate Retrieval (LSH)
When a new "suspect" image is queried, comparing its 1,000 descriptors against every single image in the database one by one would be computationally unfeasible. We solve this using FLANN (Fast Library for Approximate Nearest Neighbors) with an LSH index.
*   LSH groups similar binary descriptors into "buckets" using Hamming distance.
*   The query image's descriptors are passed through the LSH index, finding the nearest matching descriptors in the global matrix almost instantly.
*   Each matched descriptor casts a "vote" for its parent image ID.
*   The system tallies these votes. The database image with the highest number of votes (exceeding a calibrated threshold) is elected as the primary candidate for verification.

### Stage 3: Geometric Verification (RANSAC & The "Mirror Hack")
LSH voting is fast but lacks spatial awareness—it knows *what* features matched, but not *where* they are located. To prevent false positives from random feature collisions, the pipeline mandates a structural check.
*   The system loads the original, clean version of the top candidate elected by LSH.
*   It runs a brute-force cross-check matcher between the query image and the candidate image to establish direct keypoint pairings.
*   **RANSAC (Random Sample Consensus):** The system attempts to calculate a homography matrix (a geometric mapping) between the matched points. RANSAC systematically discards statistically anomalous matches (outliers) and counts the remaining structurally sound matches (inliers).
*   **The Mirror Hack:** Because standard ORB struggles with mirrored/flipped images, the pipeline physically flips the query image across multiple axes (horizontal, vertical, both) and runs the RANSAC verification on all variations, keeping the maximum inlier count found.
*   If the valid inlier count passes our final calibrated threshold, the image is officially flagged as a duplicate.

---

## Data Architecture
The dataset is cleanly isolated to prevent data leakage during threshold calibration. 

*   **`data/raw/`**: The baseline gallery containing 3,000 clean, original images.
*   **`data/train/`**: 48,000 generated variants paired with 2,400 base images. Used exclusively for calibrating the system's hyperparameter thresholds via an exhaustive grid search.
*   **`data/test/`**: 12,000 unseen variants mapped to the remaining 600 base images. Held out strictly for final system validation.
*   **`build/image_map.pkl`**: The compiled global descriptor matrix, filename mappings, and ID reference tables generated from the raw gallery.

---

## Codebase Structure

### Research & Prototyping
*   **`src/index_gallery.ipynb`**: Ingests the `data/raw/` gallery, extracts 1,000 ORB features per baseline image, and compiles them into the `image_map.pkl` database.
*   **`src/query_engine.ipynb`**: The prototyping sandbox where the core FLANN LSH search and RANSAC verification logic (including the multi-axis flipping) were developed and tested.
*   **`src/calibrate_thresholds.ipynb`**: Early exploratory analysis scripts used to map the initial distributions of LSH votes and RANSAC inliers.

### Production Execution
*   **`src/tune_pipeline.py`**: A high-efficiency calibration script. It processes the `data/train/` set, caches image descriptors in memory to prevent redundant compute cycles, and performs a nested grid search across LSH vote thresholds and RANSAC inlier thresholds to calculate the optimal F1-Score.
*   **`src/evaluate_pipeline.py`**: The final validation engine. It takes the mathematically optimal parameters discovered during tuning and applies them to the held-out `data/test/` set to generate the definitive precision, recall, and accuracy metrics.