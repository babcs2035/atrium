"""numpy 配列の型エイリアス（モジュールをまたいで使うものだけを置く）．"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

F32Array = npt.NDArray[np.float32]
I64Array = npt.NDArray[np.int64]
F64Array = npt.NDArray[np.float64]
