"""최초 신발 접촉을 후기 흡수와 분리하는 회귀 검사."""
import cv2
import numpy as np
import pytest

from contact_events import refine_contact_events


def jump(first=29, mirror=False):
    norm = np.zeros((40, 33, 3), dtype=float)
    norm[:, :, 2] = 0.95
    norm[:, :, 0] = 80 + np.arange(40)[:, None] * 3
    norm[:, [29, 30], 0] -= 8
    norm[:, [31, 32], 0] += 8
    norm[:, [29, 30, 31, 32], 1] = 300
    norm[8:25, [29, 30, 31, 32], 1] = 250
    norm[25:27, [29, 30, 31, 32], 1] = 280
    norm[25:27, first, 1] = 300
    norm[:, [27, 28], 1] = 240  # ankles are not sole-contact landmarks
    if mirror:
        norm[:, :, 0] = 400 - norm[:, :, 0]
    com = np.column_stack((80 + np.arange(40) * 3, np.full(40, 170)))
    return norm, norm[:, :, 2].copy(), np.ones(40, bool), com


def refine(data, **kw):
    return refine_contact_events(*data, 30, (400, 400), 11, 17, 23, **kw)


@pytest.mark.parametrize("first,side,kind", [(29, "L", "heel"), (31, "L", "toe"), (30, "R", "heel"), (32, "R", "toe")])
def test_first_contact_uses_either_foot_and_either_end(first, side, kind):
    result = refine(jump(first))
    assert result["landing_frame"] == 25
    assert result["contact_landmark"] == first
    assert result["contact_side"] == side
    assert result["contact_kind"] == kind
    assert result["contact_point_px"][1] == 300
    assert result["takeoff_frame"] == 8
    assert result["pre_takeoff_frame"] == 7


def test_pose_without_pixels_never_claims_verified_shoe_contact():
    result = refine(jump())
    assert result["reliable"] is False
    assert "pose" in result["method"]
    assert result["uncertainty_frames"] >= 1


def test_mirror_does_not_change_contact_frame_or_anatomical_side():
    result = refine(jump(32, mirror=True))
    assert result["landing_frame"] == 25
    assert result["contact_side"] == "R"
    assert result["contact_kind"] == "toe"


def test_hidden_other_foot_is_not_assumed_to_contact_later():
    data = jump()
    data[1][:, [30, 32]] = 0
    result = refine(data)
    assert result["reliable"] is False
    assert result["landing_frame_range"][0] < result["landing_frame"]


def test_gap_at_contact_is_not_certified_by_interpolated_coordinates():
    data = jump()
    data[2][24:28] = False
    data[1][24:28] = 0
    result = refine(data)
    assert result["reliable"] is False
    assert result["landing_frame"] >= 28
    assert result["landing_frame_range"][0] <= 24


def test_missing_pose_returns_explicit_unresolved_candidate():
    data = jump()
    data[0][:] = np.nan
    data[1][:] = 0
    data[2][:] = False
    result = refine(data)
    assert result["landing_frame"] == 23
    assert result["contact_point_px"] is None
    assert result["reliable"] is False


def test_late_settling_does_not_move_first_contact():
    data = jump()
    data[0][32:, 23:29, 1] += 55
    result = refine(data)
    assert result["landing_frame"] == 25


def test_unreadable_video_falls_back_to_honest_pose_estimate(tmp_path):
    result = refine(jump(), video=str(tmp_path / "missing.mp4"))
    assert result["landing_frame"] == 25
    assert result["reliable"] is False
    assert result["method"] == "pose_estimate"


@pytest.mark.parametrize("hidden_other_foot", [False, True])
@pytest.mark.parametrize("sole_offset", [0, 14])
def test_video_boundary_distinguishes_observed_shoe_and_both_feet(tmp_path, hidden_other_foot, sole_offset):
    data = jump()
    path = tmp_path / "shoe.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (400, 400))
    assert writer.isOpened()
    for i in range(40):
        image = np.full((400, 400, 3), 80, np.uint8)
        for a, b in ((29, 31), (30, 32)):
            x0, x1 = data[0][i, [a, b], 0].astype(int)
            bottom = int(max(data[0][i, a, 1], data[0][i, b, 1]))
            if 25 <= i <= 26:
                bottom = 294  # actual shoe is still six pixels above the floor
            bottom += sole_offset  # model foot marker can sit above the visible sole
            cv2.rectangle(image, (x0, bottom - 10), (x1, bottom), (245, 245, 245), -1)
        writer.write(image)
    writer.release()
    if hidden_other_foot:
        data[1][:, [30, 32]] = 0
    result = refine(data, video=str(path))
    assert result["landing_frame"] == 27
    assert result["method"] == "image_roi_estimate"
    assert result["contact_kind"] == "sole"
    assert result["observation_reliable"] is True
    assert result["reliable"] is (not hidden_other_foot)


def test_single_final_frame_contact_is_an_estimate_without_post_landing_window():
    data = tuple(a[:26] for a in jump())
    result = refine(data)
    assert result["landing_frame"] == 25
    assert result["contact_point_px"] is not None
    assert result["reliable"] is False
    assert result["observation_reliable"] is False


def test_heel_and_toe_keep_their_own_supported_landmark_height():
    data = jump()
    data[0][:, [29, 30], 1] -= 20
    result = refine(data)
    assert result["landing_frame"] == 25  # raised heel landmark is still supported
    assert result["contact_landmark"] == 29


@pytest.mark.parametrize('mirror', [False, True])
def test_heel_raise_and_small_toe_marker_shift_are_not_takeoff(mirror):
    data = jump(mirror=mirror)
    # During the final push the heel lifts and the model's toe marker moves
    # inside the rotating shoe, while the toe of the sole is still supported.
    direction = -1 if mirror else 1
    data[0][:, [29, 30], 0] -= 32 * direction
    data[0][:, [31, 32], 0] += 32 * direction
    data[0][6:8, [29, 30], 1] = 260
    data[0][6:8, [31, 32], 1] = 294
    result = refine(data)
    assert result['takeoff_frame'] == 8
    assert result['pre_takeoff_frame'] == 7


def test_one_visible_shoe_observes_boundaries_without_certifying_both_feet():
    data = jump(first=30)
    data[1][:, [29, 31]] = 0
    result = refine(data)
    assert result['takeoff_observed'] is True
    assert result['landing_observed'] is True
    assert result['takeoff_reliable'] is False
    assert result['reliable'] is False


def test_missing_frames_do_not_establish_observed_boundary():
    data = jump()
    data[2][24:28] = False
    data[1][24:28] = 0
    result = refine(data)
    assert result['landing_observed'] is False


@pytest.mark.parametrize('first', [30, 32])
@pytest.mark.parametrize('mirror', [False, True])
def test_observed_contact_point_comes_from_the_shoe_with_persistent_contact(first, mirror):
    data = jump(first=first, mirror=mirror)
    # An isolated false detection on the other shoe must not borrow the right
    # shoe's continuous contact evidence to become the reported contact point.
    data[0][25, 29, :2] = [50 if mirror else 350, 330]
    result = refine(data)
    assert result['landing_observed'] is True
    assert result['contact_landmark'] == first
    assert result['contact_side'] == 'R'
    assert result['contact_point_px'] == data[0][25, first, :2].tolist()


@pytest.mark.parametrize('first,outlier', [(30, 32), (32, 30)])
def test_contact_point_requires_its_own_persistent_support(first, outlier):
    data = jump(first=first)
    data[0][25, outlier, :2] = [350, 330]
    result = refine(data)
    assert result['landing_observed'] is True
    assert result['contact_landmark'] == first
    assert result['contact_point_px'] == data[0][25, first, :2].tolist()
