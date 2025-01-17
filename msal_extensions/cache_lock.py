"""Provides a mechanism for not competing with other processes interacting with an MSAL cache."""
import os
import sys
import errno
import time
import logging
from distutils.version import LooseVersion
import warnings

import portalocker


logger = logging.getLogger(__name__)


class CrossPlatLock(object):
    """Offers a mechanism for waiting until another process is finished interacting with a shared
    resource. This is specifically written to interact with a class of the same name in the .NET
    extensions library.
    """
    def __init__(self, lockfile_path):
        self._lockpath = lockfile_path
        # Support for passing through arguments to the open syscall was added in v1.4.0
        open_kwargs = ({'buffering': 0}
            if LooseVersion(portalocker.__version__) >= LooseVersion("1.4.0") else {})
        self._lock = portalocker.Lock(
            lockfile_path,
            mode='wb+',
            # In posix systems, we HAVE to use LOCK_EX(exclusive lock) bitwise ORed
            # with LOCK_NB(non-blocking) to avoid blocking on lock acquisition.
            # More information here:
            # https://docs.python.org/3/library/fcntl.html#fcntl.lockf
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            **open_kwargs)

    def try_to_create_lock_file(self):
        """Do not call this. It will be removed in next release"""
        warnings.warn("try_to_create_lock_file() will be removed", DeprecationWarning)
        return self._try_to_create_lock_file()

    def _try_to_create_lock_file(self):
        timeout = 5
        check_interval = 0.25
        current_time = getattr(time, "monotonic", time.time)
        timeout_end = current_time() + timeout
        while timeout_end > current_time():
            try:
                with open(self._lockpath, 'x'):
                    return True
            except ValueError:  # This needs to be the first clause, for Python 2 to hit it
                logger.warning("Python 2 does not support atomic creation of file")
                return False
            except FileExistsError:  # Only Python 3 will reach this clause
                logger.warning("Lock file exists, trying again after some time")
                time.sleep(check_interval)
        return False

    def __enter__(self):
        if not self._try_to_create_lock_file():
            logger.warning("Failed to create lock file")
        file_handle = self._lock.__enter__()
        file_handle.write('{} {}'.format(os.getpid(), sys.argv[0]).encode('utf-8'))
        return file_handle

    def __exit__(self, *args):
        self._lock.__exit__(*args)
        try:
            # Attempt to delete the lockfile. In either of the failure cases enumerated below, it is
            # likely that another process has raced this one and ended up clearing or locking the
            # file for itself.
            os.remove(self._lockpath)
        except OSError as ex:  # pylint: disable=invalid-name
            if ex.errno != errno.ENOENT and ex.errno != errno.EACCES:
                raise
