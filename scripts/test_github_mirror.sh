#!/bin/bash


logger() {
    echo "[$(date "+%Y-%m-%d %H:%M:%S")]:: $@"
}


get_test_repo() {
    local url
    local github_mirror=$@

    url=$(awk '{sub("term_sd_git_user/term_sd_git_repo","licyk/empty")}1' <<< "${github_mirror}")

    echo "${url}"
}


get_github_mirror_name() {
    local name
    local github_mirror=$@

    name=$(awk '{sub("/term_sd_git_user/term_sd_git_repo","")}1' <<< "${github_mirror}")

    echo "${name}"
}


test_github_mirror() {
    local dir=$1
    local github_mirror=$2
    local url
    local status
    local test_path
    local name

    url=$(get_test_repo "${github_mirror}")
    test_path="${dir}/__github_mirror_test__"
    name=$(get_github_mirror_name "${github_mirror}")

    if [[ -d "${test_path}" ]]; then
        rm -rf "${test_path}"
    fi

    logger "测试镜像源: ${name}"

    git clone "${url}" "${test_path}" --depth=1 &> /dev/null
    status=$?

    if [[ -d "${test_path}" ]]; then
        rm -rf "${test_path}"
    fi

    if [[ "${status}" == 0 ]]; then
        logger "镜像源可用"
    else
        logger "镜像源无法访问"
    fi

    return "${status}"
}


main() {
    cd "$(cd "$(dirname "$0")" ; pwd)"

    local github_mirror_list
    local mirror_status
    local mirror_url
    local start_path=$(pwd)
    local name
    local status
    local count=0
    local available

    github_mirror_list="\
        https://ghgo.xyz/https://github.com/term_sd_git_user/term_sd_git_repo \
        https://mirror.ghproxy.com/https://github.com/term_sd_git_user/term_sd_git_repo \
        https://ghproxy.net/https://github.com/term_sd_git_user/term_sd_git_repo \
        https://gh-proxy.com/https://github.com/term_sd_git_user/term_sd_git_repo \
        https://ghps.cc/https://github.com/term_sd_git_user/term_sd_git_repo \
        https://gh.idayer.com/https://github.com/term_sd_git_user/term_sd_git_repo \
        https://gitclone.com/github.com/term_sd_git_user/term_sd_git_repo \
    "

    for mirror_url in $github_mirror_list; do
        name=$(get_github_mirror_name "${mirror_url}")
        if test_github_mirror "${start_path}" "${mirror_url}"; then
            mirror_status="${mirror_status} ${name}|1"
        else
            mirror_status="${mirror_status} ${name}|0"
        fi
    done

    logger "Github 镜像源状态"
    echo "=========================================================="

    for status in $mirror_status; do
        if [[ "$(awk -F '|' '{print $NF}' <<< "${status}")" == 1 ]]; then
            available="✓"
            count=$(( count + 1))
        else
            available="×"
        fi
    
        name="$(awk -F '|' '{print $1}' <<< "${status}")"

        echo ":: ${name}: ${available}"
    done

    echo "=========================================================="
    logger "可用的 Github 镜像源数量: ${count}"

    if [[ "${count}" == 0 ]]; then
        return 1
    else
        return 0
    fi
}

main
