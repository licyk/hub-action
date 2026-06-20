#!/usr/bin/env bash
set -euo pipefail

DEFAULT_URL="https://aka.ms/vc14/vc_redist.x64.exe"
DEFAULT_DOWNLOAD_URL_SOURCE="Microsoft Learn: Latest supported Microsoft Visual C++ Redistributable downloads"
DEFAULT_DOWNLOAD_URL_SOURCE_URL="https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170#latest-supported-redistributable-version"

usage() {
  cat <<'EOF'
Usage:
  query_vcredist_x64_dlls.sh [options]

Downloads or inspects Microsoft Visual C++ 2015-2022 Redistributable x64,
extracts the x64 MSI payloads, and prints the installed DLL names from the MSI
File tables. The installer is not executed.

Options:
  --url URL              Download URL. Defaults to the official Microsoft Learn
                         latest supported x64 permalink.
  --exe PATH             Use an existing VC_redist.x64.exe instead of downloading.
  --format FORMAT        Output format: text, names, tsv, json. Default: text.
  --write-names PATH     Write generated sorted DLL names to PATH.
  --work-dir PATH        Use PATH as the parent directory for temporary files.
  --keep-workdir         Keep temporary files and print their location.
  -h, --help             Show this help.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '%s\n' "$*" >&2
}

json_escape() {
  sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g'
}

url="$DEFAULT_URL"
url_overridden=0
exe_path=""
format="text"
write_names=""
work_parent=""
keep_workdir=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      [[ $# -ge 2 ]] || fail "--url requires a value"
      url="$2"
      url_overridden=1
      shift 2
      ;;
    --exe)
      [[ $# -ge 2 ]] || fail "--exe requires a value"
      exe_path="$2"
      shift 2
      ;;
    --format)
      [[ $# -ge 2 ]] || fail "--format requires a value"
      format="$2"
      shift 2
      ;;
    --write-names)
      [[ $# -ge 2 ]] || fail "--write-names requires a value"
      write_names="$2"
      shift 2
      ;;
    --work-dir)
      [[ $# -ge 2 ]] || fail "--work-dir requires a value"
      work_parent="$2"
      keep_workdir=1
      shift 2
      ;;
    --keep-workdir)
      keep_workdir=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

case "$format" in
  text|names|tsv|json) ;;
  *) fail "--format must be one of: text, names, tsv, json" ;;
esac

required_tools=(7z cabextract msiinfo grep awk sort tail mktemp tr wc)
if [[ -z "$exe_path" ]]; then
  required_tools+=(curl)
fi
if [[ -n "$write_names" ]]; then
  required_tools+=(cp)
fi

missing_tools=()
for tool in "${required_tools[@]}"; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    missing_tools+=("$tool")
  fi
done

if [[ ${#missing_tools[@]} -gt 0 ]]; then
  fail "missing required tools: ${missing_tools[*]}"
fi

if [[ -n "$work_parent" ]]; then
  mkdir -p "$work_parent"
  run_dir="$(mktemp -d "$work_parent/vcredist-query.XXXXXX")"
else
  run_dir="$(mktemp -d /tmp/vcredist-query.XXXXXX)"
fi

cleanup() {
  if [[ "$keep_workdir" -eq 0 ]]; then
    rm -rf "$run_dir"
  else
    log "work directory: $run_dir"
  fi
}
trap cleanup EXIT

if [[ -z "$exe_path" ]]; then
  exe_path="$run_dir/VC_redist.x64.exe"
  log "downloading: $url"
  curl -fL -o "$exe_path" "$url"
else
  [[ -f "$exe_path" ]] || fail "EXE not found: $exe_path"
fi

extract_dir="$run_dir/outer"
payload_dir="$run_dir/payloads"
mkdir -p "$extract_dir" "$payload_dir"

log "extracting outer Burn bundle"
7z x -y "$exe_path" -o"$extract_dir" >/dev/null

manifest="$extract_dir/0"
[[ -f "$manifest" ]] || fail "Burn manifest not found after extraction"

xml_tags() {
  tr '<' '\n' < "$manifest"
}

payload_source_by_id() {
  local payload_id="$1"
  xml_tags | awk -v payload_id="$payload_id" '
    /^Payload[[:space:]]/ && index($0, "Id=\"" payload_id "\"") {
      pattern = "SourcePath=\"[^\"]+\""
      if (match($0, pattern)) {
        print substr($0, RSTART + 12, RLENGTH - 13)
        exit
      }
    }
  '
}

manifest_version="$(xml_tags | awk '
  /^Registration[[:space:]]/ {
    pattern = "Version=\"[^\"]+\""
    if (match($0, pattern)) {
      print substr($0, RSTART + 9, RLENGTH - 10)
      exit
    }
  }
')"

attached_container_size="$(xml_tags | awk '
  /^Container[[:space:]]/ && index($0, "Id=\"WixAttachedContainer\"") {
    pattern = "FileSize=\"[0-9]+\""
    if (match($0, pattern)) {
      print substr($0, RSTART + 10, RLENGTH - 11)
      exit
    }
  }
')"

file_version="$(7z l "$exe_path" 2>/dev/null | awk -F': ' '
  /FileVersion:/ {
    value = $2
    gsub(/[[:space:],}]/, "", value)
    print value
    exit
  }
')"

minimum_msi_source="$(payload_source_by_id "vcRuntimeMinimum_x64")"
additional_msi_source="$(payload_source_by_id "vcRuntimeAdditional_x64")"

[[ -n "$minimum_msi_source" ]] || fail "vcRuntimeMinimum_x64 payload not found in manifest"
[[ -n "$additional_msi_source" ]] || fail "vcRuntimeAdditional_x64 payload not found in manifest"

cab_contains_payloads() {
  local cab_path="$1"
  cabextract -l "$cab_path" 2>/dev/null | awk \
    -v minimum="$minimum_msi_source" \
    -v additional="$additional_msi_source" '
      $NF == minimum { found_minimum = 1 }
      $NF == additional { found_additional = 1 }
      END { exit !(found_minimum && found_additional) }
    '
}

attached_cab=""
attached_offset=""
attached_extra_bytes=""
while IFS=: read -r offset _; do
  candidate_cab="$run_dir/container_${offset}.cab"
  tail -c +$((offset + 1)) "$exe_path" > "$candidate_cab"
  if cab_contains_payloads "$candidate_cab"; then
    candidate_size="$(wc -c < "$candidate_cab" | awk '{print $1}')"

    if [[ -n "$attached_container_size" && "$candidate_size" -ge "$attached_container_size" ]]; then
      candidate_extra=$((candidate_size - attached_container_size))
    else
      candidate_extra=0
    fi

    if [[ -z "$attached_cab" || "$candidate_extra" -lt "$attached_extra_bytes" ]]; then
      attached_cab="$candidate_cab"
      attached_offset="$offset"
      attached_extra_bytes="$candidate_extra"
    fi
  fi
done < <(grep -aob "MSCF" "$exe_path")

[[ -n "$attached_cab" ]] || fail "attached payload container was not found"

log "extracting attached payload container at byte offset $attached_offset"
cabextract -q -d "$payload_dir" "$attached_cab" >/dev/null 2>"$run_dir/cabextract-attached.log"

minimum_msi="$payload_dir/$minimum_msi_source"
additional_msi="$payload_dir/$additional_msi_source"

[[ -f "$minimum_msi" ]] || fail "minimum runtime MSI not extracted: $minimum_msi_source"
[[ -f "$additional_msi" ]] || fail "additional runtime MSI not extracted: $additional_msi_source"

export_file_table() {
  local msi_path="$1"
  local group="$2"

  msiinfo export "$msi_path" File | awk -F '\t' -v group="$group" '
    $1 !~ /^(File|s72)$/ && $3 != "" {
      name = $3
      sub(/^.*\|/, "", name)
      printf "%s\t%s\t%s\t%s\n", group, name, $5, $4
    }
  ' | sort -t "$(printf '\t')" -k2,2
}

minimum_tsv="$run_dir/minimum.tsv"
additional_tsv="$run_dir/additional.tsv"
all_tsv="$run_dir/all.tsv"
generated_names="$run_dir/generated-names.txt"

export_file_table "$minimum_msi" "Minimum Runtime" > "$minimum_tsv"
export_file_table "$additional_msi" "Additional Runtime" > "$additional_tsv"
cat "$minimum_tsv" "$additional_tsv" > "$all_tsv"
awk -F '\t' '{ print $2 }' "$all_tsv" | sort -u > "$generated_names"

if [[ -n "$write_names" ]]; then
  cp "$generated_names" "$write_names"
fi

minimum_count="$(wc -l < "$minimum_tsv" | awk '{print $1}')"
additional_count="$(wc -l < "$additional_tsv" | awk '{print $1}')"
total_count="$(wc -l < "$all_tsv" | awk '{print $1}')"
package_version="${manifest_version:-${file_version:-unknown}}"
if [[ "$url_overridden" -eq 1 ]]; then
  download_url_source="Manual input"
  download_url_source_url=""
else
  download_url_source="$DEFAULT_DOWNLOAD_URL_SOURCE"
  download_url_source_url="$DEFAULT_DOWNLOAD_URL_SOURCE_URL"
fi

case "$format" in
  names)
    cat "$generated_names"
    ;;
  tsv)
    printf 'Group\tName\tVersion\tFileSize\n'
    cat "$all_tsv"
    ;;
  json)
    printf '{\n'
    printf '  "sourceUrl": "%s",\n' "$(printf '%s' "$url" | json_escape)"
    printf '  "downloadUrl": "%s",\n' "$(printf '%s' "$url" | json_escape)"
    printf '  "downloadUrlSource": "%s",\n' "$(printf '%s' "$download_url_source" | json_escape)"
    printf '  "downloadUrlSourceUrl": "%s",\n' "$(printf '%s' "$download_url_source_url" | json_escape)"
    printf '  "defaultDownloadUrl": "%s",\n' "$(printf '%s' "$DEFAULT_URL" | json_escape)"
    printf '  "defaultDownloadUrlSource": "%s",\n' "$(printf '%s' "$DEFAULT_DOWNLOAD_URL_SOURCE" | json_escape)"
    printf '  "defaultDownloadUrlSourceUrl": "%s",\n' "$(printf '%s' "$DEFAULT_DOWNLOAD_URL_SOURCE_URL" | json_escape)"
    printf '  "packageVersion": "%s",\n' "$(printf '%s' "$package_version" | json_escape)"
    printf '  "fileVersion": "%s",\n' "$(printf '%s' "${file_version:-}" | json_escape)"
    printf '  "attachedContainerOffset": %s,\n' "$attached_offset"
    printf '  "attachedContainerFileSize": %s,\n' "${attached_container_size:-0}"
    printf '  "attachedContainerExtraBytes": %s,\n' "${attached_extra_bytes:-0}"
    printf '  "payloads": {\n'
    printf '    "minimumRuntimeMsi": "%s",\n' "$(printf '%s' "$minimum_msi_source" | json_escape)"
    printf '    "additionalRuntimeMsi": "%s"\n' "$(printf '%s' "$additional_msi_source" | json_escape)"
    printf '  },\n'
    printf '  "totalFiles": %s,\n' "$total_count"
    printf '  "dlls": [\n'
    first_row=1
    while IFS="$(printf '\t')" read -r group name version size; do
      if [[ "$first_row" -eq 0 ]]; then
        printf ',\n'
      fi
      first_row=0
      printf '    {"group": "%s", "name": "%s", "version": "%s", "fileSize": %s}' \
        "$(printf '%s' "$group" | json_escape)" \
        "$(printf '%s' "$name" | json_escape)" \
        "$(printf '%s' "$version" | json_escape)" \
        "$size"
    done < "$all_tsv"
    printf '\n  ]\n'
    printf '}\n'
    ;;
  text)
    printf 'Source package: %s\n' "$url"
    printf 'Download URL source: %s\n' "$download_url_source"
    if [[ -n "$download_url_source_url" ]]; then
      printf 'Download URL source URL: %s\n' "$download_url_source_url"
    fi
    printf 'Default download URL: %s\n' "$DEFAULT_URL"
    printf 'Default download URL source: %s\n' "$DEFAULT_DOWNLOAD_URL_SOURCE"
    printf 'Default download URL source URL: %s\n' "$DEFAULT_DOWNLOAD_URL_SOURCE_URL"
    printf 'Package version: %s\n' "$package_version"
    printf 'File version: %s\n' "${file_version:-unknown}"
    printf 'Attached container offset: %s\n' "$attached_offset"
    printf 'Attached container file size: %s\n' "${attached_container_size:-unknown}"
    printf 'Attached container extra bytes: %s\n' "${attached_extra_bytes:-unknown}"
    printf 'Minimum runtime MSI payload: %s\n' "$minimum_msi_source"
    printf 'Additional runtime MSI payload: %s\n' "$additional_msi_source"
    printf 'Total DLL files: %s\n' "$total_count"
    printf '\nMinimum Runtime DLLs (%s)\n' "$minimum_count"
    awk -F '\t' '{ print $2 }' "$minimum_tsv"
    printf '\nAdditional Runtime DLLs (%s)\n' "$additional_count"
    awk -F '\t' '{ print $2 }' "$additional_tsv"
    ;;
esac
