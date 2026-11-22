"""Alchemy animation -> Blender pose-bone conversion (numpy only).

Conventions
-----------
* Alchemy matrices are row-vector (axes in rows). We work in column convention,
  so a joint frame J (3x3) has the joint's axes as columns.
* Blender bones are built with  B = [J @ C | head]  where C is a fixed rotation
  that points the Blender bone's +Y (its length axis) down the joint's +X axis
  (Max biped bones run along local X).  B columns: X_b = joint Z, Y_b = joint X,
  Z_b = joint Y.
* An animation quaternion q=(x,y,z,w) gives the *row-convention* local rotation
  Rc(q) (standard formula, read as rows); verified against bind poses of the
  sample files (pelvis 14 deg, toe 2 deg, vs. 128/180 deg when transposed).
  Column-convention local rotation is therefore  A = Rc(q).T.
* With rest local rotation L = J_p^-1 J and translation t_rest (parent joint
  frame), the pose-bone transform that reproduces the animation is
      rotation  = C^T (L^-1 A) C
      location  = C^T L^-1 (t_anim - t_rest)
  (derivation: rest_local_B = [C^T L C | C^T t_rest]; pose_local = rest_local_B
  @ basis; this holds for root bones too, with J_p = I, h_p = 0).
"""
import numpy as np

C = np.array([[0.0, 1.0, 0.0],
              [0.0, 0.0, 1.0],
              [1.0, 0.0, 0.0]])


def quat_to_rows(q):
    """(n,4) xyzw -> (n,3,3) standard rotation matrices (no transpose)."""
    q = np.asarray(q, np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    m = np.empty((len(q), 3, 3))
    m[:, 0, 0] = 1 - 2 * (y * y + z * z); m[:, 0, 1] = 2 * (x * y - z * w); m[:, 0, 2] = 2 * (x * z + y * w)
    m[:, 1, 0] = 2 * (x * y + z * w); m[:, 1, 1] = 1 - 2 * (x * x + z * z); m[:, 1, 2] = 2 * (y * z - x * w)
    m[:, 2, 0] = 2 * (x * z - y * w); m[:, 2, 1] = 2 * (y * z + x * w); m[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def mats_to_quat_wxyz(m):
    """(n,3,3) proper rotations -> (n,4) w,x,y,z (Shepperd's method, vectorised)."""
    n = len(m)
    q = np.empty((n, 4))
    tr = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    for i in range(n):
        a = m[i]
        if tr[i] > 0:
            s = np.sqrt(tr[i] + 1.0) * 2
            q[i] = [0.25 * s, (a[2, 1] - a[1, 2]) / s, (a[0, 2] - a[2, 0]) / s, (a[1, 0] - a[0, 1]) / s]
        elif a[0, 0] > a[1, 1] and a[0, 0] > a[2, 2]:
            s = np.sqrt(1.0 + a[0, 0] - a[1, 1] - a[2, 2]) * 2
            q[i] = [(a[2, 1] - a[1, 2]) / s, 0.25 * s, (a[0, 1] + a[1, 0]) / s, (a[0, 2] + a[2, 0]) / s]
        elif a[1, 1] > a[2, 2]:
            s = np.sqrt(1.0 + a[1, 1] - a[0, 0] - a[2, 2]) * 2
            q[i] = [(a[0, 2] - a[2, 0]) / s, (a[0, 1] + a[1, 0]) / s, 0.25 * s, (a[1, 2] + a[2, 1]) / s]
        else:
            s = np.sqrt(1.0 + a[2, 2] - a[0, 0] - a[1, 1]) * 2
            q[i] = [(a[1, 0] - a[0, 1]) / s, (a[0, 2] + a[2, 0]) / s, (a[1, 2] + a[2, 1]) / s, 0.25 * s]
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def make_continuous(q):
    """Flip signs so consecutive quaternions stay in the same hemisphere."""
    q = q.copy()
    for i in range(1, len(q)):
        if np.dot(q[i], q[i - 1]) < 0:
            q[i] = -q[i]
    return q


def joint_frame_from_blender(B3):
    """Blender bone rotation (armature space, 3x3 col) -> joint frame J."""
    return B3 @ C.T


def pose_basis(quats_xyzw, trans, J, Jp, head, head_p, scale, use_trans):
    """Return (rot_wxyz (n,4), loc (n,3) or None) in Blender pose-bone terms."""
    if Jp is None:
        Jp, head_p = np.eye(3), np.zeros(3)
    L = Jp.T @ J                                   # rest local rotation (column conv)
    Linv = L.T
    A = np.transpose(quat_to_rows(quats_xyzw), (0, 2, 1))   # column convention
    basis = C.T @ (Linv @ A) @ C                   # broadcast over frames
    rot = make_continuous(mats_to_quat_wxyz(basis))
    loc = None
    if use_trans and trans is not None:
        t_rest = Jp.T @ (head - head_p) / scale   # parent joint frame, file units
        d = np.asarray(trans, np.float64) - t_rest
        loc = (d @ (C.T @ Linv).T) * scale
    return rot, loc


def _resample(src_t, arr, dst_t, quat):
    n = len(src_t)
    if n == 1:
        return np.tile(arr[0], (len(dst_t), 1))
    idx = np.clip(np.searchsorted(src_t, dst_t, side="right") - 1, 0, n - 2)
    t0, t1 = src_t[idx], src_t[idx + 1]
    f = np.clip((dst_t - t0) / np.maximum(t1 - t0, 1e-12), 0.0, 1.0)[:, None]
    a, b = arr[idx], arr[idx + 1]
    if quat:
        b = b * np.where((a * b).sum(1, keepdims=True) < 0, -1.0, 1.0)
    r = a * (1 - f) + b * f
    return r / np.linalg.norm(r, axis=1, keepdims=True) if quat else r


def transfer_motion(motion, bip, J_m, t_m_rest, J_b, t_b_rest):
    """Fold the root-motion bone into Bip01 and return the new Bip01 track.

    All rotations are column-convention 3x3, translations in file units; both bones are
    roots here, so local == world. With M(t)=(A_m, t_m) and rest M0=(J_m, t_m_rest) the
    displacement is D = M(t) M0^-1, and Bip01' = D o Bip01(t).
    """
    times = bip.times if bip is not None else motion.times
    qm = _resample(motion.times, motion.quats, times, True)
    tm = (_resample(motion.times, motion.trans, times, False) if motion.trans is not None
          else np.tile(t_m_rest, (len(times), 1)))
    A_m = np.transpose(quat_to_rows(qm), (0, 2, 1))
    if bip is not None:
        qb = _resample(bip.times, bip.quats, times, True)
        A_b = np.transpose(quat_to_rows(qb), (0, 2, 1))
        tb = (_resample(bip.times, bip.trans, times, False) if bip.trans is not None
              else np.tile(t_b_rest, (len(times), 1)))
    else:
        A_b = np.tile(J_b, (len(times), 1, 1))
        tb = np.tile(t_b_rest, (len(times), 1))
    R_d = A_m @ J_m.T
    t_d = tm - np.einsum("nij,j->ni", R_d, t_m_rest)
    R_new = R_d @ A_b
    t_new = t_d + np.einsum("nij,nj->ni", R_d, tb)
    w = mats_to_quat_wxyz(np.transpose(R_new, (0, 2, 1)))        # Rc = A_col^T
    q_xyzw = make_continuous(np.stack([w[:, 1], w[:, 2], w[:, 3], w[:, 0]], axis=1))
    return type(motion)("Bip01", times, q_xyzw, t_new)


# ------------------------------------------------------------ bone-frame convention retargeting
def _octahedral():
    import itertools
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            M = np.zeros((3, 3))
            for i, (p, s) in enumerate(zip(perm, signs)):
                M[i, p] = s
            if np.linalg.det(M) > 0:
                out.append(M)
    return out


_OCT = _octahedral()


def snap24(M, tol_deg=30.0):
    """Nearest axis-aligned rotation (24 cube rotations) to M if within tol_deg, else identity.
    A bone-axis *convention* difference is such a rotation; a pose difference is arbitrary."""
    best, ba = np.eye(3), 1e9
    for C_ in _OCT:
        d = M @ C_.T
        a = np.degrees(np.arccos(np.clip((np.trace(d) - 1) / 2, -1, 1)))
        if a < ba:
            best, ba = C_, a
    return best if ba <= tol_deg else np.eye(3)


def convention_offset(std_frame, own_frame, tol_deg=30.0):
    """D = snap(S^T J): the part of the frame difference that is a pure axis relabeling."""
    if std_frame is None:
        return np.eye(3)
    return snap24(std_frame.T @ own_frame, tol_deg)


def pose_basis_rt(quats_xyzw, trans, J, Jp, head, head_p, scale, use_trans, D=None, Dp=None):
    """pose_basis with bone-convention retargeting.
    The animation is authored for standard frames S; this skeleton uses J = S D (D a pure axis relabeling).
    A' = Dp^-1 A D ;  t' = Dp^-1 t."""
    D = np.eye(3) if D is None else D
    Dp = np.eye(3) if Dp is None else Dp
    if np.allclose(D, np.eye(3)) and np.allclose(Dp, np.eye(3)):
        return pose_basis(quats_xyzw, trans, J, Jp, head, head_p, scale, use_trans)
    A = np.transpose(quat_to_rows(quats_xyzw), (0, 2, 1))
    A2 = Dp.T @ A @ D
    q2 = mats_to_quat_wxyz(np.transpose(A2, (0, 2, 1)))          # back to Alchemy xyzw for pose_basis
    q2 = np.stack([q2[:, 1], q2[:, 2], q2[:, 3], q2[:, 0]], axis=1)
    t2 = None if trans is None else np.asarray(trans, np.float64) @ Dp                # (Dp^T t) per row == t @ Dp
    return pose_basis(q2, t2, J, Jp, head, head_p, scale, use_trans)


def anim_from_basis(rot_wxyz, loc, J, Jp, head, head_p, scale, D=None, Dp=None):
    """Inverse of pose_basis_rt: Blender pose-bone basis (quaternion wxyz, location in Blender units)
    -> Alchemy local quaternion (xyzw) and translation (file units, parent joint frame)."""
    if Jp is None:
        Jp, head_p = np.eye(3), np.zeros(3)
    D = np.eye(3) if D is None else D
    Dp = np.eye(3) if Dp is None else Dp
    L = Jp.T @ J
    w, x, y, z = (np.asarray(rot_wxyz, np.float64)[:, i] for i in range(4))
    n = np.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / n, x / n, y / n, z / n
    Rb = np.empty((len(w), 3, 3))
    Rb[:, 0, 0] = 1 - 2 * (y * y + z * z); Rb[:, 0, 1] = 2 * (x * y - z * w); Rb[:, 0, 2] = 2 * (x * z + y * w)
    Rb[:, 1, 0] = 2 * (x * y + z * w); Rb[:, 1, 1] = 1 - 2 * (x * x + z * z); Rb[:, 1, 2] = 2 * (y * z - x * w)
    Rb[:, 2, 0] = 2 * (x * z - y * w); Rb[:, 2, 1] = 2 * (y * z + x * w); Rb[:, 2, 2] = 1 - 2 * (x * x + y * y)
    A2 = L @ C @ Rb @ C.T                                       # target-frame local rotation (column conv)
    A = Dp @ A2 @ D.T                                           # standard-frame local rotation
    qw = mats_to_quat_wxyz(np.transpose(A, (0, 2, 1)))
    q = make_continuous(np.stack([qw[:, 1], qw[:, 2], qw[:, 3], qw[:, 0]], axis=1))
    t = None
    if loc is not None:
        t_rest = Jp.T @ (head - head_p) / scale
        d = (np.asarray(loc, np.float64) / scale) @ (L @ C).T
        t = (t_rest + d) @ Dp.T
    return q, t
