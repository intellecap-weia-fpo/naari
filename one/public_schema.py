import graphene

from customers.graphql import TenantDiscoveryMutation, TenantDiscoveryQuery


class Query(TenantDiscoveryQuery, graphene.ObjectType):
    hello = graphene.String(default_value="Hi!")


class Mutation(TenantDiscoveryMutation, graphene.ObjectType):
    pass


schema = graphene.Schema(query=Query, mutation=Mutation)
