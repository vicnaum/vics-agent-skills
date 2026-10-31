#!/bin/bash
# Claude Code status line
# stdin: session JSON from Claude Code (see https://code.claude.com/docs/en/statusline.md)
# output: model (effort) [weekly limit % + pace] | context bar with % inside

input=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  echo "statusline: jq not found (brew install jq)"
  exit 0
fi

eval "$(printf '%s' "$input" | jq -r '@sh "model=\(.model.display_name // "Claude") used=\(.context_window.total_input_tokens // 0) size=\(.context_window.context_window_size // 200000) effort=\(.effort.level // "") weekly_raw=\(.rate_limits.seven_day.used_percentage // "") resets_at=\(.rate_limits.seven_day.resets_at // "") weekly=\((.rate_limits.seven_day.used_percentage // null) | if . then (. + 0.5 | floor) else "" end)"')"

RESET=$'\033[0m'; DIM=$'\033[2m'
MAGENTA=$'\033[35m'

model="${model% (*context)}"   # "Opus 5 (1M context)" -> "Opus 5"

(( size <= 0 )) && size=200000
pct=$(( used * 100 / size ))
(( pct > 100 )) && pct=100

# ANSI color index for a 0-100 level: green <50, yellow <80, red otherwise
level_code() {
  if   (( $1 >= 80 )); then printf 1
  elif (( $1 >= 50 )); then printf 3
  else                      printf 2
  fi
}

# weekly pace: share of the week left vs share of the limit left.
# 1.0x = on track for an even spread; 2.0x = only half the budget the clock says you should still have,
# i.e. you must spend at half the average rate until reset. Below 1.0x you have budget to spare.
WEEK=604800
pace=""
if [ -n "$weekly_raw" ] && [ -n "$resets_at" ]; then
  left=$(( resets_at - $(date +%s) ))
  if (( left >= 0 && left <= WEEK )); then
    pace=$(awk -v u="$weekly_raw" -v l="$left" -v w="$WEEK" \
      'BEGIN { b = 100 - u; p = (b > 0) ? (l * 100 / w) / b : 9.9; if (p > 9.9) p = 9.9; printf "%.1f", p }')
  fi
fi

# ANSI color index for a pace: green under 1.0x, yellow to 1.25x, red beyond
pace_code() {
  awk -v p="$1" 'BEGIN { print (p >= 1.25) ? 1 : ((p >= 1.0) ? 3 : 2) }'
}

# progress bar made of background-colored cells, with the percentage centered inside
width=10
filled=$(( (pct * width + 50) / 100 ))
(( filled > width )) && filled=$width
label="${pct}%"
lstart=$(( (width - ${#label}) / 2 ))
FILL=$'\033[1;30;4'"$(level_code "$pct")m"   # bold black on level color
TRACK=$'\033[1;37;48;5;238m'                  # bold white on dark gray
bar=""
for ((i = 0; i < width; i++)); do
  j=$(( i - lstart ))
  if (( j >= 0 && j < ${#label} )); then ch="${label:j:1}"; else ch=" "; fi
  if (( i < filled )); then bar+="${FILL}${ch}"; else bar+="${TRACK}${ch}"; fi
done
bar+="$RESET"

out="${MAGENTA}${model}${effort:+ (${effort})}${RESET}"
# weekly (7-day) rate limit; absent on API-key auth and before the first response
if [ -n "$weekly" ]; then
  out+=" "$'\033[3'"$(level_code "$weekly")m[${weekly}%${RESET}"
  [ -n "$pace" ] && out+=$'\033[3'"$(pace_code "$pace")m ×${pace}${RESET}"
  out+=$'\033[3'"$(level_code "$weekly")m]${RESET}"
fi
out+="  ${bar}"

printf '%s\n' "$out"
