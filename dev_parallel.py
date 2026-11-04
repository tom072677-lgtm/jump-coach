"""Measure whether analyzing videos in parallel actually speeds things up.
Threads only help if MediaPipe releases the GIL during inference; otherwise we
need separate processes. Measure, don't guess."""
import time
import concurrent.futures as cf
import multiprocessing as mp
import analyze_jump as A

VIDS = ["samples/slj_00003.mp4", "samples/long_test.mp4"]


def work(v):
    name = "out/par_" + v.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
    metrics, _ = A.analyze_auto(v, name)
    return metrics.get("flight_gravity_check_mps2")


if __name__ == "__main__":
    print("cpu cores:", mp.cpu_count())

    t = time.time()
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        thr = list(ex.map(work, VIDS))
    t_thr = time.time() - t
    print(f"THREADS 2 videos (current code, Figure-based plots): {t_thr:.1f}s  gravity={thr}")
    print("  ~60-80s = parallel OK ; ~140s = Figure render serialized it")
