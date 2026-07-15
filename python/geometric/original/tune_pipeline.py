import os
import csv
import pickle
import cv2
import numpy as np

# --- CONFIGURATION ---
ORB_FEATURES = 1000
TRAIN_CSV = "../data/train/metadata.csv"
TRAIN_IMAGES_FOLDER = "../data/train/images"
MAP_FILE = "../build/image_map.pkl"
OUTPUT_CSV = "threshold_tuning_results.csv"

# Note: The evaluation script uses ">" (strictly greater than), 
# so a threshold of 3 means it requires 4 or more to trigger.
VOTE_THRESHOLDS = [1, 2, 3, 4, 5, 6]
RANSAC_THRESHOLDS = [1, 2, 3, 4, 6, 8, 10]

# --- PHASE 1: DATA INGESTION & FEATURE EXTRACTION ---
print("Loading database structures...")
with open(MAP_FILE, "rb") as f:
    db_data = pickle.load(f)
    
image_id_map = db_data["image_id_map"]
image_filenames = db_data["image_filenames"]
global_descriptor_matrix = db_data["global_descriptor_matrix"]

print("Building LSH search index...")
FLANN_INDEX_LSH = 6
index_params = dict(algorithm=FLANN_INDEX_LSH, table_number=6, key_size=12, multi_probe_level=1)
flann_index = cv2.flann_Index(global_descriptor_matrix, index_params)
orb = cv2.ORB_create(nfeatures=ORB_FEATURES)

print("Reading training ground truth...")
with open(TRAIN_CSV, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"Loaded {len(rows)} evaluation pairs. Starting raw metric extraction...")

# Cache to avoid re-processing the same image if it appears in multiple rows
image_cache = {}
raw_scores = []

for idx, row in enumerate(rows):
    filename = row["copy_image"].strip()
    truth_val = str(row["is_copy"]).strip().lower()
    is_actually_fake = truth_val in ["true", "1", "yes", "t", "y"]
    
    # Only run the heavy CV pipeline if we haven't seen this image yet
    if filename not in image_cache:
        path = os.path.join(TRAIN_IMAGES_FOLDER, filename)
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        
        if img is None:
            image_cache[filename] = (0, 0)
        else:
            _, query_descriptors = orb.detectAndCompute(img, None)
            
            if query_descriptors is None:
                image_cache[filename] = (0, 0)
            else:
                # Stage 1: LSH Voting Pass
                search_params = dict(checks=50)
                idx_lsh, dist = flann_index.knnSearch(query_descriptors, knn=1, params=search_params)
                idx_lsh, dist = idx_lsh.ravel(), dist.ravel()
                
                max_image_id = max(image_filenames.keys())
                votes = np.zeros(max_image_id + 1, dtype=np.int32)
                HAMMING_THRESHOLD = 50
                
                for i in range(len(query_descriptors)):
                    if dist[i] < HAMMING_THRESHOLD:
                        votes[image_id_map[idx_lsh[i]]] += 1
                        
                winner_id = np.argmax(votes)
                max_votes = int(votes[winner_id])
                
                # Stage 2: Geometric Verification (The Mirror Hack)
                # We run this regardless of vote count here to simulate any threshold later
                max_inliers_found = 0
                if max_votes > 0: 
                    suspect_filename = image_filenames[winner_id]
                    suspect_path = os.path.join("../data/raw", suspect_filename)
                    suspect_img = cv2.imread(suspect_path, cv2.IMREAD_GRAYSCALE)
                    
                    if suspect_img is not None:
                        kp_suspect, des_suspect = orb.detectAndCompute(suspect_img, None)
                        test_variations = [img, cv2.flip(img, 1), cv2.flip(img, 0), cv2.flip(img, -1)]
                        
                        for test_img in test_variations:
                            kp_query, des_query = orb.detectAndCompute(test_img, None)
                            if des_query is not None and des_suspect is not None:
                                bf_cc = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
                                matches_ransac = bf_cc.match(des_query, des_suspect)
                                
                                if len(matches_ransac) >= 4:
                                    src_pts = np.float32([kp_query[m.queryIdx].pt for m in matches_ransac]).reshape(-1, 1, 2)
                                    dst_pts = np.float32([kp_suspect[m.trainIdx].pt for m in matches_ransac]).reshape(-1, 1, 2)
                                    M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
                                    
                                    if mask is not None:
                                        inliers = int(np.sum(mask))
                                        if inliers > max_inliers_found:
                                            max_inliers_found = inliers
                                            
                image_cache[filename] = (max_votes, max_inliers_found)

    # Fetch from cache and append the final row logic
    v_score, r_score = image_cache[filename]
    raw_scores.append({
        "is_fake": is_actually_fake,
        "votes": v_score,
        "inliers": r_score
    })
    
    if (idx + 1) % 1000 == 0 or (idx + 1) == len(rows):
        print(f"📊 Processed {idx + 1}/{len(rows)} training pairings...")


# --- PHASE 2: IN-MEMORY GRID SEARCH ---
print("\n🚀 Extraction complete. Simulating threshold combinations...")

grid_results = []
print(f"\n{'V_THRESH':<10}{'R_THRESH':<10}{'PRECISION':<12}{'RECALL':<10}{'F1-SCORE':<10}")
print("-" * 55)

for v in VOTE_THRESHOLDS:
    for r in RANSAC_THRESHOLDS:
        tp, tn, fp, fn = 0, 0, 0, 0
        
        for score in raw_scores:
            # Replicating the strictly greater than (>) logic from evaluate_pipeline.py
            predicted_fake = (score["votes"] > v) and (score["inliers"] > r)
            
            if score["is_fake"] and predicted_fake:
                tp += 1
            elif not score["is_fake"] and not predicted_fake:
                tn += 1
            elif not score["is_fake"] and predicted_fake:
                fp += 1
            elif score["is_fake"] and not predicted_fake:
                fn += 1
                
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) > 0 else 0
        
        grid_results.append({
            "vote_threshold": v,
            "ransac_threshold": r,
            "precision_pct": round(precision * 100, 2),
            "recall_pct": round(recall * 100, 2),
            "f1_score": round(f1 * 100, 2),
            "accuracy_pct": round(accuracy * 100, 2),
            "tp": tp, "tn": tn, "fp": fp, "fn": fn
        })
        
        print(f"{v:<10}{r:<10}{precision*100:<12.1f}{recall*100:<10.1f}{f1*100:<10.1f}")

# Sort results by the highest F1-Score before exporting
grid_results = sorted(grid_results, key=lambda x: x["f1_score"], reverse=True)

with open(OUTPUT_CSV, "w", newline="") as f:
    headers = ["vote_threshold", "ransac_threshold", "f1_score", "precision_pct", "recall_pct", "accuracy_pct", "tp", "tn", "fp", "fn"]
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    writer.writerows(grid_results)

print(f"\n✅ Tuning complete! Best configurations sorted by F1-Score in '{OUTPUT_CSV}'.")