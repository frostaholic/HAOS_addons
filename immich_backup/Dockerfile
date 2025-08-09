ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache \
      bash \
      jq \
      python3 \
      python3-dev \
      postgresql-client \
      dcron \
      py3-psycopg2 \
      netcat-openbsd \
      nfs-utils \
      py3-flask 
      

WORKDIR /usr/src/app

COPY export_immich_albums_db.py run.sh webgui.py ./ 

RUN chmod +x run.sh export_immich_albums_db.py

CMD [ "./run.sh" ]
