#!/usr/bin/env python3
"""Acquire an exclusive lock on a regular file, then exec a command holding it.

Usage: free-models-lock.py LOCKFILE WAIT_SECONDS CMD [ARGS...]

The lock file is opened with O_RDONLY | O_NOFOLLOW | O_NONBLOCK, creating it
with O_CREAT | O_EXCL | O_NOFOLLOW when it is absent. That combination is
chosen so the acquisition is safe against a path we do not own:

  * O_NOFOLLOW  -> a symlink at the lock path fails with ELOOP instead of being
                   followed (so a symlink swapped in at any point is refused).
  * O_NONBLOCK  -> opening a FIFO never blocks; the S_ISREG check below then
                   rejects it (so a FIFO swapped in cannot hang the run).
  * O_RDONLY    -> the open never truncates an existing file and never creates
                   the target of a dangling symlink.
  * O_CREAT|O_EXCL on the create path -> a raced creation or a pre-existing
                   file is never clobbered.

After the open the descriptor is fstat'd and must be a regular file. An
exclusive flock is then taken with a bounded wait (LOCK_NB in a poll loop). The
descriptor is dup'd to fd 9 (inheritable) and CMD is exec'd, so the lock is held
for the lifetime of the new process. fd 9 is the same number the shell scripts
expect.
"""

import fcntl
import os
import stat
import sys
import time


def fail(message, code=1):
    sys.stderr.write(message.rstrip("\n") + "\n")
    sys.exit(code)


def main(argv):
    if len(argv) < 4:
        fail("usage: free-models-lock.py LOCKFILE WAIT_SECONDS CMD [ARGS...]", 2)
    lock = argv[1]
    try:
        wait = float(argv[2])
    except ValueError:
        fail(f"invalid wait seconds: {argv[2]!r}", 2)
    cmd = argv[3:]

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    read_flags = os.O_RDONLY | nofollow | nonblock

    try:
        fd = os.open(lock, read_flags)
    except FileNotFoundError:
        # Absent: create it exclusively and never through a symlink, then
        # reopen read-only for the actual lock.
        create_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow
        try:
            cfd = os.open(lock, create_flags, 0o600)
        except FileExistsError:
            pass  # raced creation; fall through to the read-only open
        except OSError as exc:
            fail(f"could not create lock {lock}: {exc}")
        else:
            os.close(cfd)
        try:
            fd = os.open(lock, read_flags)
        except OSError as exc:
            fail(f"could not open lock {lock}: {exc}")
    except OSError as exc:
        # ELOOP (symlink), ENOTDIR, EACCES, EPERM, ...
        fail(f"could not open lock {lock}: {exc}")

    try:
        st = os.fstat(fd)
    except OSError as exc:
        fail(f"could not stat lock {lock}: {exc}")
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        fail(f"lock file {lock} is not a regular file")

    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                fail(f"could not acquire lock {lock} within {wait:g}s", 75)
            time.sleep(0.25)

    os.dup2(fd, 9, inheritable=True)
    os.set_inheritable(9, True)
    try:
        os.execvp(cmd[0], cmd)
    except OSError as exc:
        fail(f"could not exec {cmd[0]}: {exc}", 127)


if __name__ == "__main__":
    main(sys.argv)
