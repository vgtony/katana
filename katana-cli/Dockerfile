FROM python:3.7.4-slim

ENV INSTALL_PATH /katana-cli
RUN mkdir -p $INSTALL_PATH

WORKDIR $INSTALL_PATH

COPY katana-cli/. .
RUN pip install --upgrade "pip<24.1" "setuptools<58" "wheel<0.38" "Cython<3"
RUN pip install --no-build-isolation -r requirements.txt

RUN pip install --editable .

CMD /bin/bash
