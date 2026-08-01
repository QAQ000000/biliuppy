from typing import List

from biliup.engine import Plugin
from biliup.engine.upload import UploadBase, logger


@Plugin.upload(platform="Noop")
class NoopUploader(UploadBase):
    def upload(self, file_list: List[UploadBase.FileInfo]) -> List[UploadBase.FileInfo]:
        logger.info("NoopUploader")
        return file_list
