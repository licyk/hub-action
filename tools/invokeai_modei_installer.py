import os
import asyncio
import argparse
from pathlib import Path
from typing import Union
from invokeai.app.services.model_manager.model_manager_default import ModelManagerService
from invokeai.app.services.model_install.model_install_common import InstallStatus
from invokeai.app.services.model_records.model_records_sql import ModelRecordServiceSQL
from invokeai.app.services.download.download_default import DownloadQueueService
from invokeai.app.services.events.events_fastapievents import FastAPIEventService
from invokeai.app.services.config.config_default import get_config
from invokeai.app.services.shared.sqlite.sqlite_util import init_db
from invokeai.app.services.image_files.image_files_disk import DiskImageFileStorage
from invokeai.backend.util.logging import InvokeAILogger
from invokeai.app.services.invoker import Invoker



invokeai_logger = InvokeAILogger.get_logger('InvokeAI Installer')


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    normalized_filepath = lambda filepath: str(Path(filepath).absolute().as_posix())

    parser.add_argument('--invokeai-path', type = normalized_filepath, default = os.path.join(os.getcwd(), 'invokeai'), help = 'InvokeAI 根路径')
    parser.add_argument('--model-path', type=str, nargs='+', help = '安装的模型路径列表')
    parser.add_argument('--no-link', action='store_true', help = '不使用链接模式, 直接将模型安装在 InvokeAI 目录中')

    return parser.parse_args()


def get_invokeai_model_manager() -> ModelManagerService:
    invokeai_logger.info('初始化 InvokeAI 模型管理服务中')
    configuration = get_config()
    output_folder = configuration.outputs_path
    image_files = DiskImageFileStorage(f'{output_folder}/images')
    logger = InvokeAILogger.get_logger('InvokeAI', config=configuration)
    db = init_db(config=configuration, logger=logger, image_files=image_files)
    event_handler_id = 1234
    loop = asyncio.get_event_loop()
    events=FastAPIEventService(event_handler_id, loop=loop)

    model_manager = ModelManagerService.build_model_manager(
        app_config=configuration,
        model_record_service=ModelRecordServiceSQL(db=db, logger=logger),
        download_queue=DownloadQueueService(app_config=configuration, event_bus=events),
        events=FastAPIEventService(event_handler_id, loop=loop)
    )

    invokeai_logger.info('初始化 InvokeAI 模型管理服务完成')
    return model_manager


def import_model(model_manager: ModelManagerService, inplace: bool, model_path: Union[str, Path]) -> bool:
    file_name = os.path.basename(model_path)
    try:
        invokeai_logger.info(f'导入 {file_name} 模型到 InvokeAI 中')
        job = model_manager.install.heuristic_import(
            source=str(model_path),
            inplace=inplace
        )
        result = model_manager.install.wait_for_job(job)
        if result.status == InstallStatus.COMPLETED:
            invokeai_logger.info(f'导入 {file_name} 模型到 InvokeAI 成功')
            return True
        else:
            invokeai_logger.error(f'导入 {file_name} 模型到 InvokeAI 时出现了错误: {result.error}')
            return False
    except Exception as e:
        invokeai_logger.error(f'导入 {file_name} 模型到 InvokeAI 时出现了错误: {e}')
        return False


def main() -> None:
    args = get_args()
    if not os.environ.get('INVOKEAI_ROOT'):
        os.environ['INVOKEAI_ROOT'] = args.invokeai_path
    model_list = args.model_path
    install_model_to_local = args.no_link
    install_result = []
    count = 0
    task_sum = len(model_list)
    if task_sum == 0:
        invokeai_logger.info('无需要导入的模型')
        return
    invokeai_logger.info('InvokeAI 根目录: {}'.format(os.environ.get('INVOKEAI_ROOT')))
    model_manager = get_invokeai_model_manager()
    invokeai_logger.info('启动 InvokeAI 模型管理服务')
    model_manager.start(Invoker)
    invokeai_logger.info('就地安装 (仅本地) 模式: {}'.format('禁用' if install_model_to_local else '启用'))
    for model in model_list:
        count += 1
        file_name = os.path.basename(model)
        invokeai_logger.info(f'[{count}/{task_sum}] 添加模型: {file_name}')
        result = import_model(
            model_manager=model_manager,
            inplace=not install_model_to_local,
            model_path=model
        )
        install_result.append([model, file_name, result])
    invokeai_logger.info('关闭 InvokeAI 模型管理服务')
    model_manager.stop(Invoker)
    invokeai_logger.info('导入 InvokeAI 模型结果')
    print('-' * 70)
    for _, file, status in install_result:
        status = '导入成功' if status else '导入失败'
        print(f'- {file}: {status}')

    print('-' * 70)
    has_failed = False
    for _, _, x in install_result:
        if not x:
            has_failed = True
            break

    if has_failed:
        invokeai_logger.warning('导入失败的模型列表和模型路径')
        print('-' * 70)
        for model, file_name, status in install_result:
            if not status:
                print(f'- {file_name}: {model}')
        print('-' * 70)
        invokeai_logger.warning(f'导入失败的模型可尝试通过在 InvokeAI 的模型管理 -> 添加模型 -> 链接和本地路径, 手动输入模型路径并添加')

    invokeai_logger.info('导入模型结束')


if __name__ == '__main__':
    main()
