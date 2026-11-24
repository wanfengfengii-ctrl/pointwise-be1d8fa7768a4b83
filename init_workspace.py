import argparse
from pathlib import Path

from audit.workspace import initialize_workspace


def main():
    parser = argparse.ArgumentParser(description='创建或重新打开河口计量工作目录。')
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    initialize_workspace(args.directory)


if __name__ == '__main__':
    main()
