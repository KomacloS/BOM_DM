# Developer Setup

## Qt system libraries (Linux)
On Debian/Ubuntu, install:
```bash
sudo apt-get update
sudo apt-get install -y \
  libgl1 libglib2.0-0 libxkbcommon0 libxkbcommon-x11-0 \
  libdbus-1-3 libnss3 libx11-6 libx11-xcb1 libxcb1 \
  libxcb-render0 libxcb-shm0 libxcb-xfixes0 libxi6 libxtst6
```

### GitHub Actions
If you run the test suite in GitHub Actions, install the same packages before invoking the GUI:
```yaml
- name: Install Qt system libs
  run: |
    sudo apt-get update
    sudo apt-get install -y libgl1 libglib2.0-0 libxkbcommon0 libxkbcommon-x11-0 \
      libdbus-1-3 libnss3 libx11-6 libx11-xcb1 libxcb1 \
      libxcb-render0 libxcb-shm0 libxcb-xfixes0 libxi6 libxtst6
```
