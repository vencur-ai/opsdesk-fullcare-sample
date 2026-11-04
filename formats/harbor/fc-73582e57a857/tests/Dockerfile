FROM opsdesk-fullcare-sample:fullcare-0.6.4
USER root
COPY . /tests/
RUN mkdir -p /srv/data/private/fullcare && cp /tests/reference.json /srv/data/private/fullcare/fc-73582e57a857.json && chmod -R a+rX /srv/data/private
