"""Pre-extract full-video pose signals to an .npz so the auto-jump-detection
algorithm can be iterated quickly without re-running pose each time."""
import sys
import numpy as np
import analyze_jump as A

fps, size, norm, world, valid = A.extract_pose(sys.argv[1], "models/pose_landmarker_heavy.task")
np.savez_compressed(sys.argv[2], fps=fps, size=np.array(size), norm=norm, world=world, valid=valid)
print(f"saved {sys.argv[2]}: {len(norm)} frames @ {fps:.1f}fps, detect {valid.mean()*100:.0f}%")
