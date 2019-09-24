import docker
from docker.errors import ContainerError, ImageNotFound
from pathlib import Path
from tempfile import TemporaryDirectory

from sota_extractor2.errors import LatexConversionError


def ro_bind(path): return dict(bind=path, mode='ro')


def rw_bind(path): return dict(bind=path, mode='rw')


class LatexConverter:
    def __init__(self, base_path):
        # pull arxivvanity/engrafo image
        self.client = docker.from_env()
        self.base_path = Path(base_path)

    def latex2html(self, source_dir, output_dir):
        base = self.base_path
        source_dir = Path(source_dir)
        output_dir = Path(output_dir)
        volumes = {
            base / "latex2html.sh": ro_bind("/files/latex2html.sh"),
            base / "guess_main.py": ro_bind("/files/guess_main.py"),  # todo: run guess_main outside of docker
            base / "patches": ro_bind("/files/patches"),  # todo: see which patches can be dropped
            source_dir.resolve(): ro_bind("/files/ro-source"),
            output_dir.resolve(): rw_bind("/files/htmls")
        }

        output_dir.mkdir(parents=True, exist_ok=True)
        filename = "index.html"
        command = ["/files/latex2html.sh", filename]
        self.client.containers.run("arxivvanity/engrafo", command, remove=True, volumes=volumes)

    # todo: check for errors

    def clean_html(self, path):
        path = Path(path)
        volumes = {
            path.resolve(): ro_bind("/files/index.html"),
        }

        command = "timeout -t 20 -s KILL chromium-browser --headless" \
                  " --disable-gpu --disable-software-rasterizer --no-sandbox" \
                  " --timeout=30000 --dump-dom /files/index.html"
        data = self.client.containers.run("zenika/alpine-chrome:73", command, remove=True, entrypoint="",
                                          volumes=volumes)
        return data.decode('utf-8')

    def to_html(self, source_dir):
        with TemporaryDirectory() as output_dir:
            output_dir = Path(output_dir)
            try:
                self.latex2html(source_dir, output_dir)
                return self.clean_html(output_dir / "index.html")
            except ContainerError as err:
                raise LatexConversionError from err
