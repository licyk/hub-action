#!/bin/bash


logger() {
    echo "[$(date "+%Y-%m-%d %H:%M:%S")]:: $@"
}


test_huggingface_mirror() {
    local huggingface_mirror=$@
    local url
    local status

    logger "测试镜像源: ${huggingface_mirror}"

    url="${huggingface_mirror}/licyk/sd-model/resolve/main/README.md"
    curl "${url}" -o /dev/null --connect-timeout 10 --silent
    status=$?

    if [[ "${status}" == 0 ]]; then
        logger "镜像源可用"
    else
        logger "镜像源无法访问"
    fi

    return "${status}"
}


main() {
    cd "$(cd "$(dirname "$0")" ; pwd)"

    local huggingface_mirror_list
    local mirror_status
    local mirror_url
    local name
    local status
    local count=0
    local available

    huggingface_mirror_list="\
        https://hf-mirror.com \
        https://huggingface.sukaka.top \
    "

    for mirror_url in $huggingface_mirror_list; do
        if test_huggingface_mirror "${mirror_url}"; then
            mirror_status="${mirror_status} ${mirror_url}|1"
        else
            mirror_status="${mirror_status} ${mirror_url}|0"
        fi
    done

    logger "HuggingFace 镜像源状态"
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
    logger "可用的 HuggingFace 镜像源数量: ${count}"

    if [[ "${count}" == 0 ]]; then
        return 1
    else
        return 0
    fi
}

main
