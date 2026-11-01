"""Read block-puzzle screens (a grid of coloured cells, loose pieces) as text.

Text-only models can't see colours: OCR finds no text on a board of coloured squares. These
helpers turn screen pixels (RGB, one pixel per screen point) into a ``#``/``.`` map the model
can reason about. Empty cells are the darkest, uniform colour on the board; anything clearly
different is a block.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.perception.png import RGBImage

# Cells whose colours differ by less than this (sum of |R|+|G|+|B| differences) are the same
# kind. Block Blast (iPhone X): an empty cell vs the darkest block is 69 (dark theme).
SAME_COLOUR = 30


def _vivid(rgb: tuple[int, int, int]) -> int:
    hi, lo = max(rgb), min(rgb)
    return hi + (hi - lo)


def _mean(img: RGBImage, cx: float, cy: float, radius: float) -> tuple[int, int, int]:
    """Average colour of a small square around (cx, cy), clipped to the image."""
    x0, x1 = max(0, int(cx - radius)), min(img.width - 1, int(cx + radius))
    y0, y1 = max(0, int(cy - radius)), min(img.height - 1, int(cy + radius))
    r = g = b = n = 0
    px, stride = img.pixels, img.width * 3
    for y in range(y0, y1 + 1):
        row = y * stride
        for x in range(x0, x1 + 1):
            i = row + x * 3
            r += px[i]
            g += px[i + 1]
            b += px[i + 2]
            n += 1
    if n == 0:
        return (0, 0, 0)
    return (r // n, g // n, b // n)


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def _surroundings(img: RGBImage, x: float, y: float, width: float, height: float) -> int:
    """Vividness of the screen just outside a rectangle (the median, so sparkles don't count)."""
    samples = []
    for sx in range(int(x), int(x + width), 6):
        samples.append(_mean(img, sx, y - 8, 1))
        samples.append(_mean(img, sx, y + height + 8, 1))
    values = sorted(_vivid(c) for c in samples)
    return values[len(values) // 2] if values else 0


@dataclass
class Grid:
    """A board read from the screen. ``cells[r][c]`` is True when filled; geometry in points."""

    x: float
    y: float
    width: float
    height: float
    cells: list[list[bool]]

    @property
    def rows(self) -> int:
        return len(self.cells)

    @property
    def cols(self) -> int:
        return len(self.cells[0]) if self.cells else 0

    @property
    def cell_w(self) -> float:
        return self.width / self.cols

    @property
    def cell_h(self) -> float:
        return self.height / self.rows

    def center(self, row: float, col: float) -> tuple[float, float]:
        """Screen point at fractional cell coordinates (0.5, 0.5 is the middle of cell 0,0)."""
        return (self.x + col * self.cell_w, self.y + row * self.cell_h)

    def text(self) -> str:
        head = "   " + "".join(str(c % 10) for c in range(self.cols))
        lines = [head]
        for r, row in enumerate(self.cells):
            lines.append(f"{r:2d} " + "".join("#" if v else "." for v in row))
        return "\n".join(lines)

    def full_lines(self) -> tuple[list[int], list[int]]:
        rows = [r for r, row in enumerate(self.cells) if all(row)]
        cols = [c for c in range(self.cols) if all(row[c] for row in self.cells)]
        return rows, cols


def read_grid(
    img: RGBImage, x: float, y: float, width: float, height: float, rows: int, cols: int
) -> Grid:
    """Classify each cell of a rows x cols board in the given screen rectangle."""
    cw, ch = width / cols, height / rows
    # Sample the middle of each cell: edges carry bevels, grid lines and neighbours' shadows.
    radius = max(1.0, min(cw, ch) * 0.2)
    colours = [
        [_mean(img, x + (c + 0.5) * cw, y + (r + 0.5) * ch, radius) for c in range(cols)]
        for r in range(rows)
    ]
    # Empty cells all look alike and are the darkest thing on the board; blocks come in many
    # colours, some nearly as dark (Block Blast's "new best" theme: empty 56, dark green 122).
    # Boards are recessed and blocks pop out, so if even the darkest cell is brighter than the
    # screen around the board, there are no empty cells at all.
    empty = min((c for row in colours for c in row), key=_vivid)
    if _vivid(empty) > _surroundings(img, x, y, width, height):
        return Grid(x, y, width, height, [[True] * cols for _ in range(rows)])
    return Grid(
        x, y, width, height, [[_distance(c, empty) > SAME_COLOUR for c in row] for row in colours]
    )


@dataclass
class Shape:
    """A loose piece: ``cells[r][c]`` True where it has a block; ``grab`` is where to touch it."""

    index: int
    cells: list[list[bool]]
    grab: tuple[float, float]
    box: tuple[float, float, float, float] = field(default=(0.0, 0.0, 0.0, 0.0))

    @property
    def rows(self) -> int:
        return len(self.cells)

    @property
    def cols(self) -> int:
        return len(self.cells[0]) if self.cells else 0

    @property
    def size(self) -> int:
        return sum(v for row in self.cells for v in row)

    def text(self) -> str:
        return "\n".join("".join("#" if v else "." for v in row) for row in self.cells)


def _components(mask: list[list[bool]]) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (x0, y0, x1, y1, inclusive) of 8-connected True regions."""
    h = len(mask)
    w = len(mask[0]) if h else 0
    seen = [[False] * w for _ in range(h)]
    boxes = []
    for sy in range(h):
        for sx in range(w):
            if not mask[sy][sx] or seen[sy][sx]:
                continue
            stack = [(sx, sy)]
            seen[sy][sx] = True
            x0 = x1 = sx
            y0 = y1 = sy
            while stack:
                cx, cy = stack.pop()
                x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
                for nx in (cx - 1, cx, cx + 1):
                    for ny in (cy - 1, cy, cy + 1):
                        if 0 <= nx < w and 0 <= ny < h and mask[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
            boxes.append((x0, y0, x1, y1))
    return boxes


def _merge(boxes: list[tuple[int, int, int, int]], gap: int) -> list[tuple[int, int, int, int]]:
    """Join boxes closer than ``gap``: one piece's blocks can be split by thin dark seams."""
    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                a, b = merged[i], merged[j]
                if (
                    a[0] - gap <= b[2]
                    and b[0] - gap <= a[2]
                    and a[1] - gap <= b[3]
                    and b[1] - gap <= a[3]
                ):
                    merged[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


def find_shapes(
    img: RGBImage, x: float, y: float, width: float, height: float, cell: float
) -> list[Shape]:
    """Find loose pieces made of ``cell``-sized blocks in a screen rectangle, left to right."""
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(img.width, int(x + width)), min(img.height, int(y + height))
    if x1 <= x0 or y1 <= y0:
        return []
    # The background is whatever colour dominates the rectangle's top and bottom edges.
    edge = [_mean(img, sx + 0.5, y0, 0) for sx in range(x0, x1, 3)]
    edge += [_mean(img, sx + 0.5, y1 - 1, 0) for sx in range(x0, x1, 3)]
    edge.sort(key=_vivid)
    bg = edge[len(edge) // 2]
    px, stride = img.pixels, img.width * 3
    mask = []
    for sy in range(y0, y1):
        row = []
        base = sy * stride
        for sx in range(x0, x1):
            i = base + sx * 3
            # Dark-green pieces on the dark theme's grey differ by only ~46.
            row.append(_distance((px[i], px[i + 1], px[i + 2]), bg) > SAME_COLOUR)
        mask.append(row)
    shapes: list[Shape] = []
    min_side = max(2, int(cell * 0.5))
    boxes = _merge(_components(mask), max(2, int(cell * 0.35)))
    for bx0, by0, bx1, by1 in sorted(boxes):
        w, h = bx1 - bx0 + 1, by1 - by0 + 1
        if w < min_side or h < min_side:
            continue  # sparkles, text, a ripple
        if bx0 == 0 or bx1 == x1 - x0 - 1:
            continue  # cut off by the rectangle, or RootPilot's own edge glow
        cols, rows = max(1, round(w / cell)), max(1, round(h / cell))
        cw, ch = w / cols, h / rows
        cells = []
        for r in range(rows):
            line = []
            for c in range(cols):
                mx, my = int(bx0 + (c + 0.5) * cw), int(by0 + (r + 0.5) * ch)
                votes = [
                    mask[yy][xx]
                    for yy in range(max(0, my - 2), min(len(mask), my + 3))
                    for xx in range(max(0, mx - 2), min(len(mask[0]), mx + 3))
                ]
                line.append(sum(votes) * 2 > len(votes))
            cells.append(line)
        grab = (x0 + bx0 + w / 2, y0 + by0 + h / 2)
        box = (float(x0 + bx0), float(y0 + by0), float(w), float(h))
        shapes.append(Shape(len(shapes) + 1, cells, grab, box))
    return shapes


def fits(grid: Grid, shape: Shape, row: int, col: int) -> str:
    """Why ``shape`` can't go with its top-left at (row, col), or "" if it fits."""
    if row < 0 or col < 0 or row + shape.rows > grid.rows or col + shape.cols > grid.cols:
        return (
            f"a {shape.rows}x{shape.cols} piece at row {row}, col {col} would stick out of the "
            f"{grid.rows}x{grid.cols} board"
        )
    for r in range(shape.rows):
        for c in range(shape.cols):
            if shape.cells[r][c] and grid.cells[row + r][col + c]:
                return f"cell (row {row + r}, col {col + c}) is already filled"
    return ""


def placements(grid: Grid, shape: Shape) -> list[tuple[int, int]]:
    """Every (row, col) where ``shape`` fits right now."""
    return [
        (r, c)
        for r in range(grid.rows - shape.rows + 1)
        for c in range(grid.cols - shape.cols + 1)
        if not fits(grid, shape, r, c)
    ]


def place(grid: Grid, shape: Shape, row: int, col: int) -> tuple[list[list[bool]], int]:
    """The board after dropping ``shape`` at (row, col) and clearing full rows and columns.

    Returns the cells and how many lines cleared. Rows and columns clear together.
    """
    cells = [list(r) for r in grid.cells]
    for r in range(shape.rows):
        for c in range(shape.cols):
            if shape.cells[r][c]:
                cells[row + r][col + c] = True
    full_rows = [r for r in range(grid.rows) if all(cells[r])]
    full_cols = [c for c in range(grid.cols) if all(cells[r][c] for r in range(grid.rows))]
    for r in full_rows:
        cells[r] = [False] * grid.cols
    for c in full_cols:
        for r in range(grid.rows):
            cells[r][c] = False
    return cells, len(full_rows) + len(full_cols)
