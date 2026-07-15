import os
import csv
import pickle
import cv2
import numpy as np

# Define global settings, data directory structures, and classification filters
ORB_FEATURES = 1000
TEST_IMAGES_FOLDER = "../data/test/images"
TEST_CSV = "../data/test/metadata.csv"
MAP_FILE = "../build/image_map.pkl"

# --- QUICK TEST SWITCH ---
# Set to 50 for a quick 5-second test run to verify everything functions cleanly.
# Change to None when you are ready to launch the full evaluation run!
TEST_MODE_LIMIT = None  # Set to None for full evaluation, or a small integer for quick testing 

# Tuned conservative settings:
VOTE_THRESHOLD = 1       # Lowered from 15 to catch pixelated/color-swapped images in Stage 1
RANSAC_THRESHOLD = 4     # Requires 25 spatially verified inliers to confirm fraud

print("Loading database structures...")
with open(MAP_FILE, "rb") as f:
    db_data = pickle.load(f)

# Unpack global feature registries
image_id_map = db_data["image_id_map"]
image_filenames = db_data["image_filenames"]
global_descriptor_matrix = db_data["global_descriptor_matrix"]

print(f"Database loaded: {len(image_filenames)} gallery images.")

print("Building LSH search index...")
FLANN_INDEX_LSH = 6
# Configure locality-sensitive hashing structures for binary data matching
index_params = dict(algorithm=FLANN_INDEX_LSH, table_number=6, key_size=12, multi_probe_level=1)
flann_index = cv2.flann_Index(global_descriptor_matrix, index_params)
orb = cv2.ORB_create(nfeatures=ORB_FEATURES)

print("Loading ground truth from metadata.csv...")
ground_truth = {}
# Parse the validation csv file by header fields and resolve truth flags safely
with open(TEST_CSV, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        filename = row["copy_image"].strip()
        truth_val = str(row["is_copy"]).strip().lower()
        is_copy = truth_val in ["true", "1", "yes", "t", "y"]
        ground_truth[filename] = is_copy
            
        # Stop loading early if we are running in quick test mode
        if TEST_MODE_LIMIT and (i + 1) >= TEST_MODE_LIMIT:
            break

print(f"Loaded {len(ground_truth)} test cases.")

# Initialize counters for the system confusion matrix
tp, tn, fp, fn = 0, 0, 0, 0
failures = []

print("\n🚀 Evaluating images against registry...")

processed_count = 0
for filename, is_actually_fake in ground_truth.items():
    path = os.path.join(TEST_IMAGES_FOLDER, filename)
    
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        continue
        
    processed_count += 1
    
    # --- STAGE 1: LSH Fast Scan ---
    _, query_descriptors = orb.detectAndCompute(img, None)
    if query_descriptors is None:
        if is_actually_fake: fn += 1
        else: tn += 1
        continue
        
    search_params = dict(checks=50)
    idx, dist = flann_index.knnSearch(query_descriptors, knn=1, params=search_params)
    idx, dist = idx.ravel(), dist.ravel()
    
    # Allocate voting entries to match the maximum map key index to prevent out-of-bounds errors
    max_image_id = max(image_filenames.keys())
    votes = np.zeros(max_image_id + 1, dtype=np.int32)
    HAMMING_THRESHOLD = 50
    
    # Distribute matching feature votes across target gallery IDs
    for i in range(len(query_descriptors)):
        if dist[i] < HAMMING_THRESHOLD:
            votes[image_id_map[idx[i]]] += 1
            
    winner_id = np.argmax(votes)
    max_votes = votes[winner_id]
    
    predicted_fake = False
    
    # Evaluate Stage 1 trigger passthrough
    if max_votes > VOTE_THRESHOLD:
        # --- STAGE 2: Targeted Spatial Verification via RANSAC ---
        suspect_filename = image_filenames[winner_id]
        suspect_path = os.path.join("../data/raw", suspect_filename)
        suspect_img = cv2.imread(suspect_path, cv2.IMREAD_GRAYSCALE)
        
        if suspect_img is not None:
            kp_suspect, des_suspect = orb.detectAndCompute(suspect_img, None)
            
            # The Mirror Hack: Test the original image first. 
            # Test the original image, plus every major flip/rotation axis
            test_variations = [
                img, 
                cv2.flip(img, 1),  # Horizontal mirror
                cv2.flip(img, 0),  # Vertical mirror
                cv2.flip(img, -1), # Both (180 degree rotation)
            ]
            
            for test_img in test_variations:
                kp_query, des_query = orb.detectAndCompute(test_img, None)
                
                if des_query is not None and des_suspect is not None:
                    bf_cc = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
                    matches_ransac = bf_cc.match(des_query, des_suspect)
                    
                    # Check geometric coherence if enough mutual points match up
                    if len(matches_ransac) >= 4:
                        src_pts = np.float32([kp_query[m.queryIdx].pt for m in matches_ransac]).reshape(-1, 1, 2)
                        dst_pts = np.float32([kp_suspect[m.trainIdx].pt for m in matches_ransac]).reshape(-1, 1, 2)
                        M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
                        
                        # Confirm classification flag if geometric structural consistency threshold is surpassed
                        if mask is not None and np.sum(mask) > RANSAC_THRESHOLD:
                            predicted_fake = True
                            break  # Target verified as fake; exit variation loop early

    # Map engine performance outcomes into corresponding confusion matrix buckets
    if is_actually_fake and predicted_fake:
        tp += 1
    elif not is_actually_fake and not predicted_fake:
        tn += 1
    elif not is_actually_fake and predicted_fake:
        fp += 1
        failures.append(f"❌ FALSE POSITIVE: Clean file '{filename}' flagged as fake (Votes: {max_votes})")
    elif is_actually_fake and not predicted_fake:
        fn += 1
        failures.append(f"❌ FALSE NEGATIVE: Fake file '{filename}' snuck through undetected (Votes: {max_votes})")

    if processed_count % 1000 == 0:
        print(f"📊 Processed {processed_count}/{len(ground_truth)} images...")

# Compute final operational diagnostic statistics
total = tp + tn + fp + fn
accuracy = (tp + tn) / total if total > 0 else 0
precision = tp / (tp + fp) if (tp + fp) > 0 else 0
recall = tp / (tp + fn) if (tp + fn) > 0 else 0

print("\n==================================================")
print("📊 FINAL SYSTEM PERFORMANCE REPORT")
print("==================================================")
print(f"Total Test Images: {total}")
print(f"Accuracy:          {accuracy * 100:.1f}%")
print(f"Precision:         {precision * 100:.1f}%  (How reliable are our fraud alerts?)")
print(f"Recall:            {recall * 100:.1f}%  (What percentage of fakes did we catch?)")
print("--------------------------------------------------")
print(f" True Positives (Caught Fakes):     {tp}")
print(f" True Negatives (Allowed Clean):    {tn}")
print(f" False Positives (Bad Blocks):      {fp}  <-- Aim for 0!")
print(f" False Negatives (Missed Fakes):    {fn}  <-- Aim for 0!")
print("==================================================")

if failures:
    print("\n🚨 SAMPLE OF MISCLASSIFICATIONS:")
    for f in failures[:10]:
        print(f)