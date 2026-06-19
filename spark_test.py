from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .master("local[*]")
    .appName("test")
    .getOrCreate()
)

df = spark.createDataFrame([(1,), (2,), (3,)], ["id"])

print(df.count())

spark.stop()