import graphene
from graphene import relay
from graphene_sqlalchemy import SQLAlchemyConnectionField
from graphene_sqlalchemy import SQLAlchemyObjectType
from webob_graphql import serve_graphql_request

#from galaxy import model as galaxymodel
from galaxy.web import _future_expose_api_anonymous_and_sessionless as expose_api_anonymous_and_sessionless
from galaxy.web.base.controller import BaseAPIController


class GraphQLController(BaseAPIController):
    class User(SQLAlchemyObjectType):
        class Meta:
            model = self.app.model.Workflow
            interfaces = (relay.Node, )


    class Query(graphene.ObjectType):
        node = relay.Node.Field()
        all_users = SQLAlchemyConnectionField(User)


    schema = graphene.Schema(query=Query)

    @expose_api_anonymous_and_sessionless
    def index(self, trans, request):
        context = {'session': request.session}
        return serve_graphql_request(request, schema, batch_enabled=True, context_value=context)
