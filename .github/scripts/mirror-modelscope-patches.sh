#!/usr/bin/env bash

set -euo pipefail

: "${MODELSCOPE_TOKEN:?MODELSCOPE_TOKEN is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"

MODELSCOPE_DATASET="${MODELSCOPE_DATASET:-AnxunBCX/PCL_Nex}"
MODELSCOPE_BRANCH="${MODELSCOPE_BRANCH:-master}"
SOURCE_DIR="${SOURCE_DIR:-static/patch}"
DESTINATION="static/patch"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Patch source directory does not exist: $SOURCE_DIR" >&2
  exit 1
fi

shopt -s nullglob
patch_files=("$SOURCE_DIR"/*.patch)
for patch_file in "${patch_files[@]}"; do
  patch_name="$(basename "$patch_file")"
  if [[ ! "$patch_name" =~ ^[0-9a-f]{64}_[0-9a-f]{64}\.patch$ ]]; then
    echo "Invalid patch file name: $patch_name" >&2
    exit 1
  fi
  if [[ ! -s "$patch_file" ]]; then
    echo "Patch file is empty: $patch_name" >&2
    exit 1
  fi
done

askpass="$RUNNER_TEMP/modelscope-patch-askpass.sh"
mirror_dir="$RUNNER_TEMP/modelscope-patch-mirror"
trap 'rm -f "$askpass"' EXIT
umask 077
cat > "$askpass" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  *Username*) printf '%s\n' 'oauth2' ;;
  *Password*) printf '%s\n' "${MODELSCOPE_TOKEN:?}" ;;
  *) exit 1 ;;
esac
EOF
chmod 700 "$askpass"

export GIT_ASKPASS="$askpass"
export GIT_TERMINAL_PROMPT=0
export GIT_LFS_SKIP_SMUDGE=1
git clone --depth 1 --branch "$MODELSCOPE_BRANCH" \
  "https://www.modelscope.cn/datasets/$MODELSCOPE_DATASET.git" \
  "$mirror_dir"
git -C "$mirror_dir" lfs install --local
git -C "$mirror_dir" lfs track '*.patch'

mkdir -p "$mirror_dir/$DESTINATION"
find "$mirror_dir/$DESTINATION" -maxdepth 1 -type f -name '*.patch' -delete
for patch_file in "${patch_files[@]}"; do
  cp -- "$patch_file" "$mirror_dir/$DESTINATION/$(basename "$patch_file")"
done

git -C "$mirror_dir" config user.name 'github-actions[bot]'
git -C "$mirror_dir" config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git -C "$mirror_dir" add .gitattributes
git -C "$mirror_dir" add -A "$DESTINATION"
if git -C "$mirror_dir" diff --cached --quiet; then
  echo 'ModelScope already contains the current static patch set.'
  exit 0
fi

git -C "$mirror_dir" commit -m 'Sync PCL2 Nex static update patches'
for attempt in 1 2 3; do
  if git -C "$mirror_dir" push origin "HEAD:$MODELSCOPE_BRANCH"; then
    break
  fi
  if [[ "$attempt" -eq 3 ]]; then
    echo 'Unable to push the ModelScope patch mirror after three attempts.' >&2
    exit 1
  fi
  git -C "$mirror_dir" pull --rebase origin "$MODELSCOPE_BRANCH"
done

{
  echo '## ModelScope patch mirror'
  echo
  echo "Mirrored ${#patch_files[@]} static update patch(es) to $MODELSCOPE_DATASET/$DESTINATION."
} >> "$GITHUB_STEP_SUMMARY"
