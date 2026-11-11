#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: templates.py
# License: 5G-MAG Public License (v1.0)
#
'''Store of Content Preparation Templates (TS 26.512 clause 4.5.3)

The AS only keeps the templates (body and media type) and serves their identifiers: no media processing is done.
Each template is kept in a file named after its identifier, the media type in a second file, so templates survive a restart.
The identifiers of destroyed templates are remembered too (a destroyed identifier answers 410 and cannot be reused).
'''
import json
import os


class TemplateStore:
    def __init__(self, directory):
        self.__dir = directory
        self.__bodies = os.path.join(directory, 'body')
        self.__types = os.path.join(directory, 'type')
        self.__destroyed_file = os.path.join(directory, 'destroyed.json')
        os.makedirs(self.__bodies, mode=0o755, exist_ok=True)
        os.makedirs(self.__types, mode=0o755, exist_ok=True)
        self.__destroyed = set()
        try:
            with open(self.__destroyed_file, 'r', encoding='utf-8') as fh:
                self.__destroyed = set(str(i) for i in json.load(fh))
        except (FileNotFoundError, ValueError, OSError):
            pass

    def ids(self):
        return sorted(e.name for e in os.scandir(self.__bodies) if e.is_file())

    def have(self, template_id):
        return os.path.isfile(os.path.join(self.__bodies, template_id))

    def wasDestroyed(self, template_id):
        return template_id in self.__destroyed

    def get(self, template_id):
        '''Return (body bytes, media type) or None'''
        if not self.have(template_id):
            return None
        with open(os.path.join(self.__bodies, template_id), 'rb') as fh:
            body = fh.read()
        with open(os.path.join(self.__types, template_id), 'r', encoding='utf-8') as fh:
            return body, fh.read()

    def put(self, template_id, body, media_type):
        '''Store a template; return True if it was created or changed, False if identical to what is stored'''
        if self.get(template_id) == (body, media_type):
            return False
        self.__write(os.path.join(self.__types, template_id), media_type.encode('utf-8'))
        self.__write(os.path.join(self.__bodies, template_id), body)
        return True

    def delete(self, template_id):
        os.remove(os.path.join(self.__bodies, template_id))
        os.remove(os.path.join(self.__types, template_id))
        self.__destroyed.add(template_id)
        self.__write(self.__destroyed_file, json.dumps(sorted(self.__destroyed)).encode('utf-8'))

    @staticmethod
    def __write(name, data):
        tmp = name + '.tmp'
        with open(tmp, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, name)
