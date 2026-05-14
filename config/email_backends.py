import os
from datetime import datetime

from django.core.mail.backends.filebased import EmailBackend as FileBasedEmailBackend


class EmlFileEmailBackend(FileBasedEmailBackend):
    def _get_filename(self):
        if self._fname is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            fname = "%s-%s.eml" % (timestamp, abs(id(self)))
            self._fname = os.path.join(self.file_path, fname)
        return self._fname
