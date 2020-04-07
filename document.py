# -*- coding: utf-8 -*-

import six
from tornado.concurrent import run_on_executor

from motorengine.metaclasses import DocumentMetaClass
from motorengine.errors import InvalidDocumentError, LoadReferencesRequiredError
from bson.objectid import ObjectId
from motorengine.fields import DateTimeField

AUTHORIZED_FIELDS = [
    '_id', '_values', '_reference_loaded_fields', 'is_partly_loaded'
]

DISABLED_FIELDS = ['id']

class BaseDocument(object):
    def __init__(
        self, _is_partly_loaded=False, _reference_loaded_fields=None, **kw
    ):
        """
        :param _is_partly_loaded: is a flag that indicates if the document was
        loaded partly (with `only`, `exlude`, `fields`). Default: False.
        :param _reference_loaded_fields: dict that contains projections for
        reference fields if any. Default: None.
        :param kw: pairs of fields of the document and their values
        """
        for key in DISABLED_FIELDS:
            kw.pop(key, None)
            self._fields.pop(key, None)

        from motorengine.fields.dynamic_field import DynamicField

        _id = kw.pop('_id', None)
        self.set_id(_id)
        self._values = {}
        self._changed_values = set()

        self.is_partly_loaded = _is_partly_loaded

        if _reference_loaded_fields:
            self._reference_loaded_fields = _reference_loaded_fields
        else:
            self._reference_loaded_fields = {}

        for key, field in list(self._fields.items()):
            if isinstance(field, DateTimeField) and field.auto_now_on_update:
                self._changed_values.add(key)

            if callable(field.default):
                self._values[field.name] = field.default()
            else:
                self._values[field.name] = field.default

        for key, value in list(kw.items()):
            if key in self._fields:
                self._values[key] = value
            elif self.__allow_undefined_fields__:
                self._fields[key] = DynamicField(db_field="_%s" % key.lstrip('_'))
                self._values[key] = value
            else:
                pass

    # old
    @classmethod
    @run_on_executor
    def ensure_index(cls, callback=None):
        cls.objects.ensure_index(callback=callback)

    # new
    @classmethod
    async def ensure_indexes(cls):
        await cls.objects.ensure_indexes()

    @property
    def is_lazy(self):
        return self.__class__.__lazy__

    def is_list_field(self, field):
        from motorengine.fields.list_field import ListField
        return isinstance(field, ListField) or (isinstance(field, type) and issubclass(field, ListField))

    def is_reference_field(self, field):
        from motorengine.fields.reference_field import ReferenceField
        return isinstance(field, ReferenceField) or (isinstance(field, type) and issubclass(field, ReferenceField))

    def is_embedded_field(self, field):
        from motorengine.fields.embedded_document_field import EmbeddedDocumentField
        return isinstance(field, EmbeddedDocumentField) or (isinstance(field, type) and issubclass(field, EmbeddedDocumentField))

    @classmethod
    def from_son(cls, dic, _is_partly_loaded=False, _reference_loaded_fields=None):
        field_values = {}
        _object_id = dic.pop('_id', None)
        for name, value in list(dic.items()):
            field = cls.get_field_by_db_name(name)
            if field:
                field_values[field.name] = field.from_son(value)
            else:
                field_values[name] = value
        field_values["_id"] = _object_id

        return cls(
            _is_partly_loaded=_is_partly_loaded,
            _reference_loaded_fields=_reference_loaded_fields,
            **field_values
        )

    def to_son(self) -> dict:
        """Summary
        convert the document object into a dict type data, which can be used the motor driver
        to easily interact with db. Normaly, only the defined field will be converted, unless
        you set `__allow_undefined_fields__` to True.
        
        Returns:
            dict: Description
        """
        data = dict()

        for name, field in list(self._fields.items()):
            value = self.get_field_value(name)

            if field.sparse and value is None:
                continue

            data[field.db_field] = field.to_son(value)

        return data

    def to_son_changed_values(self):
        """Summary
        only convert updated field in the document into a dict data for being used in partly update
        document instead of replace document into db.
        
        Returns:
            TYPE: Description
        """
        data = dict()

        for name in self._changed_values:
            value = self.get_field_value(name)

            if name in self._fields:
                field = self._fields[name]

                if field.sparse and value is None:
                    continue

                data[field.db_field] = field.to_son(value)

        return data


    def validate(self):
        return self.validate_fields()

    def validate_fields(self):
        for name, field in list(self._fields.items()):

            value = self.get_field_value(name)

            if field.required and field.is_empty(value):
                raise InvalidDocumentError("Field '%s' is required." % name)
            if not field.validate(value):
                raise InvalidDocumentError("Field '%s' must be valid." % name)

        return True

    # new
    @run_on_executor
    def save(self, callback, alias=None, upsert=False):
        '''
        Creates or updates the current instance of this document.
        '''
        self.objects.save(self, callback=callback, alias=alias, upsert=upsert)

    async def save(self, alias=None, upsert=False) -> ObjectId:
        '''
        Creates or updates the current instance of this document.
        '''
        _id = await self.objects.save(self, alias=alias, upsert=upsert)
        return _id



    async def update(self, alias=None, upsert=False):
        '''
        Updates the changed fileds in the current instance of this document.
        The difference between save and update,
        `save` actully use replace_one method in motor to do whole document update, so
        you must care the document you want to `save` to make sure the fields not missing
        in your expectation,
        `update` actully use update_one method in motor to do part document update, so
        it will do update on fields which you want to change and do not affect other fields.
        Now to decide on which fields to be updated, it should use left-right assignment method,
        
        Usage:
            class PhotoModel(Document):
                name = StringField(required=True)


            class AlbumModel(Document):
                __collection__ = (
                    "albums"
                )  # optional. if no collection is specified, class name is used.
                title = StringField(required=False) 
                photos = ListField(EmbeddedDocumentField(PhotoModel))
                created_at = DateTimeField(auto_now_on_insert=True, tz=datetime.timezone.utc)
                updated_at = DateTimeField(
                    auto_now_on_insert=True, auto_now_on_update=True, tz=datetime.timezone.utc
                )
            
            photos = [PhotoModel(name="1"), PhotoModel(name="2")]
            album = AlbumModel(title="aaaaa", photos=photos)
            album.save()
        
            
            album.title = "bbbbbb"
            photos[0].name = "3"
            photos[1].name = "4"
            album.photos = photos #!!! must do the assignment even album.photos already refer to photos
            album.update()

        Args:
            alias (None, optional): Description
            upsert (bool, optional): Description
        '''
        await self.objects.update_self(self, alias=alias, upsert=upsert)


    # old
    @run_on_executor
    def delete(self, callback, alias=None):
        '''
        Deletes the current instance of this Document.

        .. testsetup:: saving_delete_one

            import tornado.ioloop
            from motorengine import *

            class User(Document):
                __collection__ = "UserDeletingInstance"
                name = StringField()

            io_loop = tornado.ioloop.IOLoop.instance()
            connect("test", host="localhost", port=27017, io_loop=io_loop)

        .. testcode:: saving_delete_one

            def handle_user_created(user):
                user.delete(callback=handle_user_deleted)

            def handle_user_deleted(number_of_deleted_items):
                try:
                    assert number_of_deleted_items == 1
                finally:
                    io_loop.stop()

            def create_user():
                user = User(name="Bernardo")
                user.save(callback=handle_user_created)

            io_loop.add_timeout(1, create_user)
            io_loop.start()
        '''
        self.objects.remove(instance=self, callback=callback, alias=alias)
    
    # new
    async def delete(self, alias=None):
        '''
        Deletes the current instance of this Document.

        .. testsetup:: saving_delete_one

            import tornado.ioloop
            from motorengine import *

            class User(Document):
                __collection__ = "UserDeletingInstance"
                name = StringField()

            io_loop = tornado.ioloop.IOLoop.instance()
            connect("test", host="localhost", port=27017, io_loop=io_loop)

        .. testcode:: saving_delete_one

            def handle_user_created(user):
                user.delete(callback=handle_user_deleted)

            def handle_user_deleted(number_of_deleted_items):
                try:
                    assert number_of_deleted_items == 1
                finally:
                    io_loop.stop()

            def create_user():
                user = User(name="Bernardo")
                user.save(callback=handle_user_created)

            io_loop.add_timeout(1, create_user)
            io_loop.start()
        '''
        deleted_count = await self.objects.remove(instance=self, alias=alias)
        return deleted_count

    def fill_values_collection(self, collection, field_name, value):
        collection[field_name] = value

    def fill_list_values_collection(self, collection, field_name, value):
        if field_name not in collection:
            collection[field_name] = []
        collection[field_name].append(value)

    def handle_load_reference(self, callback, references, reference_count, values_collection, field_name, fill_values_method=None):
        if fill_values_method is None:
            fill_values_method = self.fill_values_collection

        def handle(*args, **kw):
            fill_values_method(values_collection, field_name, args[0])

            if reference_count > 0:
                references.pop()

            if len(references) == 0:
                callback({
                    'loaded_reference_count': reference_count,
                    'loaded_values': values_collection
                })

        return handle

    # old
    @run_on_executor
    def load_references(self, fields=None, callback=None, alias=None):
        if callback is None:
            raise ValueError("Callback can't be None")

        references = self.find_references(document=self, fields=fields)
        reference_count = len(references)

        if not reference_count:
            callback({
                'loaded_reference_count': reference_count,
                'loaded_values': []
            })
            return

        for dereference_function, document_id, values_collection, field_name, fill_values_method in references:
            dereference_function(
                document_id,
                callback=self.handle_load_reference(
                    callback=callback,
                    references=references,
                    reference_count=reference_count,
                    values_collection=values_collection,
                    field_name=field_name,
                    fill_values_method=fill_values_method
                )
            )

    # TODO:
    # new
    async def load_references(self, fields=None, alias=None):
        references = self.find_references(document=self, fields=fields)
        reference_count = len(references)

        if not reference_count:
            print({
                'loaded_reference_count': reference_count,
                'loaded_values': []
            })
            return

        for dereference_function, document_id, values_collection, field_name, fill_values_method in references:
            
            document = await dereference_function(document_id)

            if fill_values_method is None:
                fill_values_method = self.fill_values_collection

            fill_values_method(values_collection, field_name, document)

            if reference_count > 0:
                references.pop()

            if len(references) == 0:
                print({
                    'loaded_reference_count': reference_count,
                    'loaded_values': values_collection
                })

        return


    def find_references(self, document, fields=None, results=None):
        if results is None:
            results = []

        if not isinstance(document, Document):
            return results

        if fields:
            fields = [
                (field_name, field)
                for field_name, field in list(document._fields.items())
                if field_name in fields
            ]
        else:
            fields = [field for field in list(document._fields.items())]

        for field_name, field in fields:
            self.find_reference_field(document, results, field_name, field)
            self.find_list_field(document, results, field_name, field)
            self.find_embed_field(document, results, field_name, field)

        return results

    def _get_load_function(self, document, field_name, document_type):
        """Get appropriate method to load reference field of the document"""
        if field_name in document._reference_loaded_fields:
            # there is a projection for this field
            fields = document._reference_loaded_fields[field_name]
            return document_type.objects.fields(**fields).get
        return document_type.objects.get

    def find_reference_field(self, document, results, field_name, field):
        if self.is_reference_field(field):
            value = document._values.get(field_name, None)
            load_function = self._get_load_function(
                document, field_name, field.reference_type
            )
            if value is not None:
                results.append([
                    load_function,
                    value,
                    document._values,
                    field_name,
                    None
                ])

    def find_list_field(self, document, results, field_name, field):
        from motorengine.fields.reference_field import ReferenceField
        if self.is_list_field(field):
            values = document._values.get(field_name)
            if values:
                document_type = values[0].__class__
                if isinstance(field._base_field, ReferenceField):
                    document_type = field._base_field.reference_type
                    load_function = self._get_load_function(
                        document, field_name, document_type
                    )
                    for value in values:
                        results.append([
                            load_function,
                            value,
                            document._values,
                            field_name,
                            self.fill_list_values_collection
                        ])
                    document._values[field_name] = []
                else:
                    self.find_references(document=document_type, results=results)

    def find_embed_field(self, document, results, field_name, field):
        if self.is_embedded_field(field):
            value = document._values.get(field_name, None)
            if value:
                self.find_references(document=value, results=results)

    def get_field_value(self, name):
        if name not in self._fields:
            raise ValueError("Field %s not found in instance of %s." % (
                name,
                self.__class__.__name__
            ))

        field = self._fields[name]
        value = field.get_value(self._values.get(name, None))

        return value

    def set_id(self, value):
        """Summary
        Automatically check value type, it can be ObjectId or a string. As desined, this function
        should be called when the document is instantiated or when a new document is saved into db
        to keep consistent `_id` with db. (the db will return `_id` which is decided by mongoDB)

        `_id` will represents the ObjectId, while `id` will represent the string format of the `_id`.
        They should be in pair and consistence. The virtual `id` field help us use easily than `_id`,
        when using `Pydantic` model. It will never be saved into db or set into document self._fields.

        Args:
            value (TYPE): Description
        """
        if value is None:
            self._id = None
            self.id = None

        elif isinstance(value, ObjectId):
            self._id = value
            self.id = str(self._id)
        
        else:
            self.id = str(value)
            self._id = ObjectId(self.id)

    def __getattribute__(self, name):
        # required for the next test
        if name in ['_fields']:
            return object.__getattribute__(self, name)

        if name in self._fields:
            field = self._fields[name]
            is_reference_field = self.is_reference_field(field)
            value = field.get_value(self._values.get(name, None))

            if is_reference_field and value is not None and not isinstance(value, field.reference_type):
                message = "The property '%s' can't be accessed before calling 'load_references'" + \
                    " on its instance first (%s) or setting __lazy__ to False in the %s class."

                raise LoadReferencesRequiredError(
                    message % (name, self.__class__.__name__, self.__class__.__name__)
                )

            return value

        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        from motorengine.fields.dynamic_field import DynamicField

        if name not in AUTHORIZED_FIELDS and name not in self._fields and name not in DISABLED_FIELDS and self.__allow_undefined_fields__:
            self._fields[name] = DynamicField(db_field="_%s" % name)

        if name in self._fields:
            self._values[name] = value
            self._changed_values.add(name)
            return

        object.__setattr__(self, name, value)

    @classmethod
    def get_field_by_db_name(cls, name):
        for field_name, field in list(cls._fields.items()):
            if name == field.db_field or name.lstrip("_") == field.db_field:
                return field
        return None

    @classmethod
    def get_fields(cls, name, fields=None):
        from motorengine import EmbeddedDocumentField, ListField
        from motorengine.fields.dynamic_field import DynamicField

        if fields is None:
            fields = []

        if '.' not in name:
            dyn_field = DynamicField(db_field="_%s" % name)
            fields.append(cls._fields.get(name, dyn_field))
            return fields

        field_values = name.split('.')
        dyn_field = DynamicField(db_field="_%s" % field_values[0])
        obj = cls._fields.get(field_values[0], dyn_field)
        fields.append(obj)

        if isinstance(obj, EmbeddedDocumentField):
            obj.embedded_type.get_fields(".".join(field_values[1:]), fields=fields)

        if isinstance(obj, ListField):
            obj.item_type.get_fields(".".join(field_values[1:]), fields=fields)

        return fields


class Document(six.with_metaclass(DocumentMetaClass, BaseDocument)):
    '''
    Base class for all documents specified in MotorEngine.
    '''
    pass
