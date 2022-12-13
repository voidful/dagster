from dagster import Definitions, job, op


@op
def an_op():
    pass


@job
def success_job():
    an_op()


defs = Definitions(jobs=[success_job])
