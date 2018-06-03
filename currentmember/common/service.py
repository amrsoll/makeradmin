from flask import abort, jsonify, request, Flask
from werkzeug.exceptions import NotFound, MethodNotAllowed
import pymysql
import sys
import os
import requests
import socket
import signal
from time import sleep
from functools import wraps

SERVICE_USER_ID = -1


class DB:
    def __init__(self, host, name, user, password):
        self.host = host
        self.name = name
        self.user = user
        self.password = password
        self.connection = None

    def connect(self):
        # Note autocommit is required to make updates from other services visible immediately
        self.connection = pymysql.connect(host=self.host.split(":")[0], port=int(self.host.split(":")[1]), db=self.name, user=self.user, password=self.password, autocommit=True)

    def cursor(self):
        self.connection.ping(reconnect=True)
        return self.connection.cursor()


class APIGateway:
    def __init__(self, host, key, host_frontend, host_backend):
        self.host = host
        self.host_frontend = host_frontend
        self.host_backend = host_backend
        self.auth_headers = {"Authorization": "Bearer " + key}

    def get_frontend_url(self, path):
        return "http://" + self.host_frontend + "/" + path

    def get(self, path, payload=None):
        return requests.get('http://' + self.host + "/" + path, params=payload, headers=self.auth_headers)

    def post(self, path, payload):
        return requests.post('http://' + self.host + "/" + path, json=payload, headers=self.auth_headers)

    def put(self, path, payload):
        return requests.put('http://' + self.host + "/" + path, json=payload, headers=self.auth_headers)

    def delete(self, path):
        return requests.delete('http://' + self.host + "/" + path, headers=self.auth_headers)


class Service:
    def __init__(self, name, url, port, version, db, gateway, debug, frontend):
        self.name = name
        self.url = url
        self.port = port
        self.version = version
        self.db = db
        self.debug = debug
        self.gateway = gateway
        self.frontend = frontend
        self.app = Flask(name, static_url_path=self.full_path("static"))

    def full_path(self, path):
        return "/" + self.url + ("/" + path if path != "" else "")

    def route(self, path: str, **kwargs):
        path = self.full_path(path)

        def wrapper(f):
            return self.app.route(path, **kwargs)(f)
        return wrapper

    def register(self):
        # Frontend services do not register themselves as API endpoints
        if not self.frontend:
            payload = {
                "name": self.name,
                "url": self.url,
                "endpoint": "http://" + socket.gethostname() + ":" + str(self.port) + "/",
                "version": self.version
            }
            r = self.gateway.post("service/register", payload)
            if not r.ok:
                raise Exception("Failed to register service: " + r.text)

    def unregister(self):
        if not self.frontend:
            payload = {
                "url": self.url,
                "version": self.version
            }
            r = self.gateway.post("service/unregister", payload)
            if not r.ok:
                raise Exception("Failed to unregister service: " + r.text)

    def wrap_error_codes(self):
        # Pretty ugly way to wrap all abort(code, message) calls so that they return proper json reponses
        def create_wrapper(status_code):
            @self.app.errorhandler(status_code)
            def custom_error(error):
                response = jsonify({'status': error.description})
                response.status_code = status_code
                return response
        for i in range(400, 500):
            try:
                create_wrapper(i)
            except:
                pass

    def add_route_list(self):
        '''Adds an endpoint (/routes) for listing all routes of the service'''
        @self.route("routes")
        def site_map():
            return jsonify({"data": [{"url": rule.rule, "methods": list(rule.methods)} for rule in self.app.url_map.iter_rules()]})

    def serve_indefinitely(self):
        self.add_route_list()
        self.wrap_error_codes()

        def signal_handler(signal, frame):
            eprint("Closing database connection")
            self.db.connection.close()
            self.unregister()
            exit(0)

        for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]:
            signal.signal(sig, signal_handler)

        # self.app.run(host='0.0.0.0', debug=self.debug, port=self.port, use_reloader=False)


def assert_get(data, key):
    if key not in data:
        abort(400, "Missing required parameter " + key)

    return data[key]


def read_config():
    try:
        db = DB(
            host=os.environ["MYSQL_HOST"],
            name=os.environ["MYSQL_DB"],
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASS"],
        )

        gateway = APIGateway(os.environ["APIGATEWAY"], os.environ["BEARER"], os.environ["HOST_FRONTEND"], os.environ["HOST_BACKEND"])

        debugStr = os.environ["APP_DEBUG"].lower()
        if debugStr == "true" or debugStr == "false":
            debug = debugStr == "true"
        else:
            raise Exception(f"APP_DEBUG environment variable must be either 'true' or 'false'. Found '{debugStr}'")
    except Exception as e:
        eprint("Missing one or more configuration environment variables")
        eprint(e)
        sys.exit(1)
    return db, gateway, debug


def eprint(s, **kwargs):
    ''' Print to stderr and flush the output stream '''
    print(s, **kwargs, file=sys.stderr)
    sys.stderr.flush()


def create(name, url, port, version):
    db, gateway, debug = read_config()
    service = Service(name, url, port, version, db, gateway, debug, False)

    try:
        service.unregister()
    except:
        # Python sometimes starts so quickly that the API-Gateway module has not managed to initialize itself yet
        # So the initial unregister call may fail. If it does, wait for a few seconds and then try again.
        sleep(2)
        service.unregister()

    eprint("Registering service...")
    service.register()


    eprint("Connecting to database...")
    db.connect()
    return service


def create_frontend(url, port):
    db, gateway, debug = read_config()
    service = Service("frontend", url, port, None, db, gateway, debug, True)
    eprint("Connecting to database...")
    db.connect()
    return service


def route_helper(f, json=False, status="ok"):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if json:
            data = request.get_json()
            if data is None:
                abort(400, "missing json")

        try:
            res = f(data, *args, **kwargs) if json else f(*args, **kwargs)
        except NotFound:
            return jsonify({"status": "not found"}), 404
        except Exception as e:
            eprint(e)
            raise e

        if res is None:
            return jsonify({"status": status})
        else:
            return jsonify({
                "data": res,
                "status": status
            })

    return wrapper


DEFAULT_WHERE = object()


class Entity:
    def __init__(self, table, columns, read_columns=[], read_transforms={}, write_transforms={}, exposed_column_names={}, allow_delete=True):
        '''
        table: The name of the table in the database
        columns: List of column names in the database (excluding the id column which is implicit)
        read_transforms: Map from column names to lambda functions which are run on every value that is read from the database
        write_transforms: Map from column names to lambda functions which are run on every value that is written to the database
        exposed_column_names: Map from column names to the name of the field as seen by users of this API (e.g you can rename 'id' to show up as 'object_id' when using the API for example)
        '''
        self.table = table
        self.columns = columns
        self.all_columns = self.columns[:] + read_columns
        self.all_columns.insert(0, "id")
        self.column_name2exposed_name = exposed_column_names
        self.read_transforms = read_transforms
        self.write_transforms = write_transforms
        self.db = None
        self.allow_delete = allow_delete

        for c in self.all_columns:
            if c not in self.column_name2exposed_name:
                self.column_name2exposed_name[c] = c
            if c not in read_transforms:
                read_transforms[c] = lambda x: x
            if c not in write_transforms:
                write_transforms[c] = lambda x: x

        self.fields = ",".join(self.columns)
        self.all_fields = ",".join(self.all_columns)

    def get(self, id):
        with self.db.cursor() as cur:
            cur.execute(f"SELECT {self.all_fields} FROM {self.table} WHERE id=%s", (id,))
            item = cur.fetchone()
            if item is None:
                raise NotFound(f"No item with id '{id}' in table {self.table}")

            return self._convert_to_dict(item)

    def _convert_to_row(self, data):
        return [self.write_transforms[col](data[self.column_name2exposed_name[col]]) for col in self.columns]

    def _convert_to_dict(self, row):
        return {
            self.column_name2exposed_name[self.all_columns[i]]: self.read_transforms[self.all_columns[i]](row[i]) for i in range(len(self.all_columns))
        }

    def put(self, data, id):
        with self.db.cursor() as cur:
            values = self._convert_to_row(data)
            cols = ','.join(col + '=%s' for col in self.columns)
            cur.execute(f"UPDATE {self.table} SET {cols} WHERE id=%s", (*values, id))

    def post(self, data):
        with self.db.cursor() as cur:
            values = self._convert_to_row(data)
            cols = ','.join('%s' for col in self.columns)
            cur.execute(f"INSERT INTO {self.table} ({self.fields}) VALUES({cols})", values)
            return self.get(cur.lastrowid)

    def delete(self, id):
        if not self.allow_delete:
            return MethodNotAllowed()

        with self.db.cursor() as cur:
            cur.execute(f"UPDATE {self.table} SET deleted_at=CURRENT_TIMESTAMP WHERE id=%s", (id,))

    def list(self, where=DEFAULT_WHERE, where_values=[]):
        if where == DEFAULT_WHERE:
            where = "deleted_at IS NULL" if self.allow_delete else None

        with self.db.cursor() as cur:
            where = "WHERE " + where if where is not None else ""
            cur.execute(f"SELECT {self.all_fields} FROM {self.table} {where}", where_values)
            rows = cur.fetchall()
            return [self._convert_to_dict(row) for row in rows]

    def add_routes(self, service, endpoint):
        # Note: Many methods here return other methods that we then call.
        # The endpoint keyword argument is just because flask needs something unique, it doesn't matter what it is for our purposes
        id_string = "<int:id>" if endpoint == "" else "/<int:id>"
        service.route(endpoint + id_string, endpoint=endpoint+".get", methods=["GET"])(route_helper(self.get, status="ok"))
        service.route(endpoint + id_string, endpoint=endpoint+".put", methods=["PUT"])(route_helper(self.put, json=True, status="updated"))
        if self.allow_delete:
            service.route(endpoint + id_string, endpoint=endpoint+".delete", methods=["DELETE"])(route_helper(self.delete, status="deleted"))
        service.route(endpoint + "", endpoint=endpoint+".post", methods=["POST"])(route_helper(self.post, json=True, status="created"))
        service.route(endpoint + "", endpoint=endpoint+".list", methods=["GET"])(route_helper(self.list, status="ok"))
