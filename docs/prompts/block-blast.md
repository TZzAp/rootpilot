# Prompt: one round of Block Blast!

A task for RootPilot that plays one round of *Block Blast!* (Classic mode) until it loses, then
reports the score. The model plays by itself: RootPilot only gives it eyes (`read_grid`) and
hands (`drag_shape`).

**Device-specific.** The coordinates are for an iPhone X (375×812 points) and Block Blast
7.5.6. On another screen size, measure the board and the piece tray again (see the end).

**Before you start**
- Settings tab on the control page: max steps **200**, max minutes **40**, token budget
  **3000** (thousand). A round is 40–90 moves and every step resends the growing history.
- The overlay must be installed (`rootpilot doctor` → "edge glow … loaded"): it supplies the
  screen pixels.
- Kill switch: lock the phone, or press volume 4× quickly.

## The prompt

```text
Play exactly ONE round of the game Block Blast! (Classic mode) until you lose, then stop and report.

GET TO THE BOARD
1. launch_app com.blockpuzzle.us.ios, then observe.
2. On the main menu ("Adventure" and "Classic" buttons) tap "Classic". Never tap Adventure, GOGO! Blast, Rotate Rings, the picture icon or the gear.
3. If a popup covers the board during play (e.g. "Survey"), close it with its X in the top-right corner of the popup (the "Survey" popup: tap x=310, y=238). Never answer surveys, never buy anything.
4. Play the round that is on screen. If the board already has blocks, continue it. Never restart.

SEE THE BOARD
OCR cannot see the blocks. Use read_grid, always with exactly these arguments:
{"x":22,"y":191.5,"width":331,"height":331.5,"rows":8,"cols":8,"pieces":{"x":15,"y":570,"width":345,"height":110,"cell":18.4},"drag_lift":93.5,"drag_gain":1.5}
It returns the 8x8 board ('#' filled, '.' empty, row 0 = top, col 0 = left) and the pieces you can place, numbered left to right, each with the (row,col) places where it fits.

MOVE
drag_shape {"piece":N,"row":R,"col":C} puts the top-left corner of piece N's box on cell (R,C). Only use a place from that piece's "fits at" list. It returns the new board and pieces: use them for the next move (piece numbers change after every move). Call read_grid again only if drag_shape reports something unexpected.

RULES
- You get 3 pieces at a time. Place all 3 in any order, then 3 new ones appear. Pieces cannot be rotated.
- A completely filled row or column disappears and scores. Clearing several at once scores much more.
- The game ends when none of the remaining pieces fits anywhere.

STRATEGY (decide quickly, think about the next pieces too)
- First look for a move that completes a row or column; prefer the one that clears the most.
- Before each move, check that your other remaining pieces will still fit afterwards. If a piece has very few places, place it first.
- Keep the board open: build along the edges and corners, keep the middle free, don't leave single empty holes that only a 1x1 piece could fill.
- Always try to keep room for a 3x3 square and for a 5-long bar (the hardest pieces).

END OF THE ROUND
- After the last move the game shows an advert (an app's name, "App Store", "Install") and then the result screen. Never tap an advert, not even to close it or skip it: call wait (5 seconds) again and again, up to a minute, until "Best Score!" or the score screen appears.
- The round is over when the tool says "No piece fits anywhere", or a result / game-over screen appears. Then do not tap anything: no Revive, no Continue, no ads, no Play again, no new round.
- observe once to read the final score, then call done with: the final score, how many pieces you placed, how many times you cleared lines, and why the round ended.
- If drag_shape says a piece "did not drop" twice in a row, observe: a popup may cover the game. Close it with its X as above (never through an ad).
- Nobody is watching: never use shell or ask_user. If the board looks wrong, wait 2 seconds and read_grid again.
```

## How the numbers were measured

From screen captures through the overlay (one pixel per point) on an iPhone X:

| What | Value |
|---|---|
| Board (8×8) | x 22–353, y 191.5–523 points; one cell ≈ 41.4 pt |
| Piece tray | y 570–680; pieces centred near x 78 / 187 / 296, y 620; one block ≈ 18.4 pt |
| Dragging | the piece jumps 93.5 pt above the finger and moves **1.5×** as far as the finger (measured at five finger positions; `drag_shape` works back from these) |
| Cancel a drag | release with the piece outside the board (below the tray): it goes back, no move |
| Cell colours | empty cells are the darkest colour on the board and darker than the screen around it; blocks are brighter. Normal theme: empty (34,40,74), background (65,82,144). "New best" dark theme: empty (47,46,51), background (77,76,81), dark-green blocks (54,88,71) |

`read_grid` and `drag_shape` work for other block puzzles too; measure the board rectangle, the
tray, the tray's block size, and how a dragged piece follows the finger.

## First round (2026-11-01, iPhone X, `deepseek-flash`, default thinking)

| | |
|---|---|
| Final score | **2532** ("Best Score!"; the previous best on the phone was 1234) |
| Pieces placed | 41, every one where the model aimed; 1 out-of-place move refused by `drag_shape` |
| Line clears | 14 (4 of them two or more lines at once) |
| Time | 16.4 min of play, 75 model calls, 7.4 s average per call |
| Tokens | 2.75M input, 94k output |
| End | a 1×5 bar with no row of 5 empty cells; the model stopped on the result screen, as told |

What went wrong, and is fixed: at 1395 points the game switched to its dark "new best" theme and
the board reader, tuned on the blue theme, saw dark-green blocks as empty; the run was stopped,
the reader changed to "empty = the darkest colour on the board", and the same round continued.
The context had grown to 90k tokens per call (old boards, replayed reasoning); with trimming the
continued run stayed under 21k.

## Second round: screenshots on, after the speed work (same day)

| | First round (text only) | Second round (screenshots) |
|---|---|---|
| Final score | 2532 | **4014** |
| Pieces / line clears | 41 / 14 | 76 / 29 |
| Seconds per piece | 24.1 | 19.1 |
| Model time per move (median) | 3.7 s | 6.6 s |
| Input tokens | 2.75M (37k per call) | 1.70M (20k per call) |

One round each, and the pieces are random, so the score difference is not proof. After the last
move an advert appeared and the model tapped it, opening an App Store page, which it closed (no
install); the prompt now says to wait out adverts instead of closing them.
