import datetime
import re
from humongolus import Field, FieldException, Document, import_class, _settings
from bson.objectid import ObjectId
from gridfs import GridFS
import iso8601

class MinException(FieldException): pass
class MaxException(FieldException): pass

def parse_phone(number, append_plus_one=True):
    try:
        phonePattern = re.compile(r'''
                        # don't match beginning of string, number can start anywhere
            (\d{3})     # area code is 3 digits (e.g. '800')
            \D*         # optional separator is any number of non-digits
            (\d{3})     # trunk is 3 digits (e.g. '555')
            \D*         # optional separator
            (\d{4})     # rest of number is 4 digits (e.g. '1212')
            \D*         # optional separator
            (\d*)       # extension is optional and can be any number of digits
            $           # end of string
            ''', re.VERBOSE)
        res = phonePattern.search(number).groups()
        st = "".join(res)
        if append_plus_one:
            if st.startswith("1"): return "+%s" % st
            else: return "+1%s" % st
        else:
            return st
    except Exception as e:
        raise e

class Char(Field):
    _max=None
    _min=None
    _type = unicode
    _exception_display = "string"

    def clean(self, val, doc=None):
        try:
            val = self._type(val)
            if self._max != None and len(val) > self._max: raise MaxException("must be less than %s" % self._max)
            if self._min != None and len(val) < self._min: raise MinException("must be greater than %s" % self._min)
            return val
        except FieldException as e: raise e
        except: raise FieldException("%s is not a valid %s" % (val, self._exception_display))

class Integer(Char):
    _type=int
    _exception_display ="integer"

    def clean(self, val, doc=None):
        try:
            if val:
                val = self._type(val)
                if self._max != None and val > self._max: raise MaxException("must be less than %s" % self._max)
                if self._min != None and val < self._min: raise MinException("must be greater than %s" % self._min)
            return val
        except FieldException as e: raise e
        except: raise FieldException("%s is not a valid %s" % (val, self._exception_display))

class Float(Integer):
    _type=float
    _exception_display ="float"

class Date(Field):

    def clean(self, val, doc=None):
        if val is None: return None # A date field can be None unless it's required
        try:
            if isinstance(val, datetime.datetime): return val
            if isinstance(val, basestring): return iso8601.parse_date(val)
            return datetime.datetime(val)
        except: raise FieldException("%s: invalid datetime" % val)


class Boolean(Field):
    _default = False
    def clean(self, val, doc=None):
        if val is None: return None # A boolean field can be None unless it's required
        try:
            if isinstance(val, bool): return val
            elif isinstance(val, basestring): return val not in ['0', 'false', 'False', 'no', 'f', 'n', 'N', 'F']
            return bool(val)
        except: raise FieldException("%s invalid boolean" % val)

class Geo(Field):
    def clean(self, val, doc=None):
        try:
            if isinstance(val, list):
                if len(val) == 2:
                    return val
                else: raise FieldException("to many values: %s" % val)
            else: raise FieldException("must be type array")
        except: raise FieldException("%s must be array" % val)

class TimeStamp(Date):

    def _save(self, namespace):
        if self._value == None:
            self._value = datetime.datetime.utcnow()

        return super(TimeStamp, self)._save(namespace)

class DocumentId(Field):
    _type = None

    def clean(self, val, doc=None):
        val = val._id if hasattr(val, '_id') else val
        if val:
            v = ObjectId(val)
        else: raise FieldException("value cannot be None")
        return v

    def __call__(self):
        if not self._value is None and self._type:
            return self._type(id=self._value)
        else: raise FieldException("Cannot instantiate %s with id %s" % (self._type, self._value))


class AutoIncrement(Integer):
    _collection = None

    def _save(self, namespace):
        if self._value == None:
            col = self._collection if self._collection else "sequence"
            res = self._conn[_settings.AUTOINC_DB_NAME][col].find_and_modify({"field":self._name}, {"$inc":{"val":1}}, upsert=True, new=True, fields={"val":True})
            self._value = res['val']

        return super(AutoIncrement, self)._save(namespace)

class DynamicDocument(Field):

    def clean(self, val, doc=None):
        if val is None: return None # A DynamicDocument field can be None unless it's required
        if isinstance(val, Document):
            if val._id != None:
                cls = "%s.%s" % (val.__module__, val.__class__.__name__)
                return {"cls":cls, "_id":val._id}
            else: raise FieldException("Document does not have an id. Be sure to save first.")
        elif isinstance(val, dict):
            return val
        else: raise FieldException("%s is not a valid document type" % val.__class__.__name__)

    def __call__(self):
        if isinstance(self._value, dict):
            cls = import_class(self._value['cls'])
            if not (hasattr(self, "__dyninst__") and self.__dyninst__._id == self._value['_id']):
                self.__dyninst__ = cls.find_one({"_id": self._value['_id']})
            return self.__dyninst__
        elif self._value == None:
            return self._value
        else: raise Exception("Bad Value: %s" % self._value)

    def _get_value(self):
        return self()

    def _save(self, namespace):
        if hasattr(self, "__dyninst__"):
            self.__dyninst__.save()
        return super(DynamicDocument, self)._save(namespace)


class DocumentReference(DynamicDocument):
    _type = None

    def clean(self, val, doc=None):
        if val is None: return None
        if self._type and not isinstance(val, dict):
            if isinstance(val, basestring) or isinstance(val, ObjectId):
                try:
                    # Try to convert val into an ObjectId
                    if type(val) == ObjectId:
                        v = val
                    else:
                        v = ObjectId(val)
                    # if it passes:
                    val = self._type.find_one({"_id": v})
                    if val != None:
                        cls = "%s.%s" % (val.__module__, val.__class__.__name__)
                        return {"cls":cls, "_id":val._id}
                    else: raise FieldException("%s is not a valid %s" % (v, self._type))
                except FieldException as e:
                    raise e
                except:
                    pass

            if type(val) != self._type:
                raise FieldException("%s is not a valid %s" % (type(val), self._type))
        return super(DocumentReference, self).clean(val, doc)


class Choice(Char):
    _choices = []

    def clean(self, val, doc=None):
        val = super(Choice, self).clean(val, doc=doc)
        vals = [opt['value'] if isinstance(opt, dict) else opt for opt in self._choices]
        if not val in vals: raise FieldException("%s is not a valid option")
        return val

    def get_choices(self, render=None):
        return self._choices

class ModelChoice(DocumentId):
    _type = None
    _render = None
    _fields = None
    _query = {}
    _sort = {}

    def get_choices(self, render=None):
        if render:
            cur = self._type.find(self._query, fields=self._fields)
            cur = cur.sort(self._sort) if self._sort else cur
            return [render(i) for i in cur]
        else: raise FieldException("no render method available")

class CollectionChoice(Choice):
    _db = None
    _collection = None
    _render = None
    _fields = None
    _query = {}
    _sort = {}

    def get_choices(self, render=None):
        if render:
            print self._db
            print self._collection
            cur = self._conn[self._db][self._collection].find(self._query, fields=self._fields)
            cur = cur.sort(self._sort) if self._sort else cur
            return [render(i) for i in cur]
        else: raise FieldException("no render method available")

class Regex(Char):
    _reg = None
    _disp_error = None

    def clean(self, val, doc=None):
        val = super(Regex, self).clean(val, doc)
        if not self._reg.search(val): raise FieldException("%s: pattern not found" % val if not self._disp_error else self._disp_error)
        return val

class Email(Regex):
    _disp_error = "Invalid Email Address"
    _reg = re.compile(
    r"(^[-!#$%&'*/=?^_`{}|~0-9A-Z]+(\.[-!#$%&'*/=?^_`{}|~0-9A-Z]+)*"  # dot-atom
    r'|^"([\001-\010\013\014\016-\037!#-\[\]-\177]|\\[\001-011\013\014\016-\177])*"' # quoted-string
    r')@(?:[A-Z0-9-]+\.)+[A-Z]{2,6}$', re.IGNORECASE)  # domain

class Phone(Char):
    _append_plus_one = True

    def clean(self, val, doc=None):
        val = super(Phone, self).clean(val, doc)
        try:
            return parse_phone(val, self._append_plus_one)
        except: raise FieldException("%s is not a valid format" % val)

class File(DocumentId):
    _database = None
    _collection = "fs"
    _args = {}

    def clean(self, val, doc=None):
        if val is None: return None # A File field can be None unless it's required
        if not self._database: raise FieldException("database is required")
        if isinstance(val, ObjectId): return val
        try:
            f = GridFS(self._database, collection=self._collection)
            self._args["filename"] = self._args.get("filename", self._name)
            return f.put(val, **self._args)
        except Exception as e:
            raise FieldException(e.message)

    def exists(self):
        f = GridFS(self._database, collection=self._collection)
        return f.exists(self._value)

    def delete(self):
        f = GridFS(self._database, collection=self._collection)
        return f.delete(self._value)

    def __call__(self):
        if isinstance(self._value, ObjectId):
            f = GridFS(self._database, collection=self._collection)
            return f.get(self._value)
        else:
            raise FieldException("No file associated")

    def __getattr__(self, key):
        f = GridFS(self._database, collection=self._collection)
        return getattr(f, key)
