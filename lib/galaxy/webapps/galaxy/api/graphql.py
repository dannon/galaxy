import graphene
from graphene import relay
from graphene_sqlalchemy import SQLAlchemyConnectionField
from graphene_sqlalchemy import SQLAlchemyObjectType
from graphene_sqlalchemy.converter import convert_sqlalchemy_type, get_column_doc, is_column_nullable

# from graphene import (ID, Boolean, Dynamic, Enum, Field, Float, Int, List, String)
from graphene import String
from webob_graphql import serve_graphql_request

from galaxy import model as galaxymodel
from galaxy.web import _future_expose_api_raw as expose_api_raw
from galaxy.web.base.controller import BaseAPIController

from galaxy.model.custom_types import UUIDType


@convert_sqlalchemy_type.register(UUIDType)
def convert_column_to_string(type, column, registry=None):
    return String(description=get_column_doc(column),
                  required=not(is_column_nullable(column)))


class Workflow(SQLAlchemyObjectType):
    class Meta:
        model = galaxymodel.Workflow
        interfaces = (relay.Node, )

    @classmethod
    def get_query(cls, model, info, **args):
        return kbDatastoreSql.get_tool_library_by_tag(return_hidden=False, **args)


class Query(graphene.ObjectType):
    node = relay.Node.Field()
    all_workflows = SQLAlchemyConnectionField(Workflow)


schema = graphene.Schema(query=Query)


class GraphQLController(BaseAPIController):
    @expose_api_raw
    def index(self, trans, **kwargs):
        context = {'session': trans.session}
        return serve_graphql_request(trans.request, schema, batch_enabled=True, context_value=context)
