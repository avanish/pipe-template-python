from pipeline.transforms.source import Source
from pipeline.transforms.group_by_id import GroupById
from pipeline.transforms.resample import Resample
from pipeline.transforms.create_features import CreateFeatures
from pipeline.transforms.trim_stationary_periods import TrimStationaryPeriods
from pipeline.transforms.sink import Sink
from pipeline.objects.location_record import LocationRecordsFromDicts
from pipeline.objects.feature import FeaturesToDicts
from pipeline.schemas.output import build as build_output_schema
from apache_beam import io
from apache_beam.pvalue import AsList
from apache_beam import Flatten
from apache_beam import Map
from apache_beam.transforms.window import TimestampedValue
from pipe_tools.io import WriteToBigQueryDatePartitioned

import datetime




class PipelineDefinition():

    def __init__(self, options):
        self.options = options

    def create_queries(self):
        template = """
        SELECT
          FLOAT(TIMESTAMP_TO_MSEC(timestamp)) / 1000  AS timestamp,
          STRING(mmsi)               AS id,
          lat                        AS lat,
          lon                        AS lon,
          speed                      AS speed_knots,
          course                     AS course,
          distance_from_shore / 1000 AS distance_from_shore_km
        FROM
          TABLE_DATE_RANGE([world-fishing-827:{table}.], 
                                TIMESTAMP('{start:%Y-%m-%d}'), TIMESTAMP('{end:%Y-%m-%d}'))
        WHERE
          lat   IS NOT NULL AND lat >= -90.0 AND lat <= 90.0 AND
          lon   IS NOT NULL AND lon >= -180.0 AND lon <= 180.0 AND
          speed IS NOT NULL AND speed >=0 AND speed <= 100.0 AND
          course IS NOT NULL AND course >= 0 AND course < 360 AND
          distance_from_shore IS NOT NULL AND distance_from_shore >= 0 AND distance_from_shore <= 20000.0
        """
        start_date = datetime.datetime.strptime(self.options.start_date, '%Y-%m-%d') 
        start_window = start_date - datetime.timedelta(days=1)
        end_date= datetime.datetime.strptime(self.options.end_date, '%Y-%m-%d') 
        while start_window <= end_date:
            end_window = min(start_window + datetime.timedelta(days=999), end_date)
            query = template.format(table=self.options.source_table, start=start_window, end=end_window)
            yield query
            start_window = end_window + datetime.timedelta(days=1)


    def build(self, pipeline):
        # sink = Sink(
        #     table=self.options.sink_table,
        #     write_disposition=self.options.sink_write_disposition,
        # )
        sink = WriteToBigQueryDatePartitioned(
            temp_gcs_location=self.options.temp_gcs_location,
            table=self.options.sink_table,
            write_disposition="WRITE_TRUNCATE",
            schema=build_output_schema()
            )


        sources = [(pipeline | "Read_{}".format(i) >> io.Read(io.gcp.bigquery.BigQuerySource(query=x)))
                        for (i, x) in enumerate(self.create_queries())]

        (
            sources
            | Flatten()
            | LocationRecordsFromDicts()
            | Resample(increment_min=15, max_gap_min=120)
            | TrimStationaryPeriods(max_distance_km=0.8, min_period_minutes=60*48)
            | CreateFeatures()
            | FeaturesToDicts()
            | Map(lambda elem: TimestampedValue(elem, elem['timestamp']))
            | "WriteToSink" >> sink
        )

        return pipeline
