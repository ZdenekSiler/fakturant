FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY src/chart.min.js   /usr/share/nginx/html/static/js/chart.min.js
COPY src/app.js         /usr/share/nginx/html/static/js/app.js
COPY src/ares.js        /usr/share/nginx/html/static/js/ares.js
COPY src/csv_import.js  /usr/share/nginx/html/static/js/csv_import.js
COPY src/dashboard.js   /usr/share/nginx/html/static/js/dashboard.js
COPY src/app.css        /usr/share/nginx/html/static/css/app.css

EXPOSE 80
