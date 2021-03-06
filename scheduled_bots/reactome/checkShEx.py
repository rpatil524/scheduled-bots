import subprocess
import os
import jsonasobj
import pandas as pd
import requests
from SPARQLWrapper import SPARQLWrapper, JSON
from ShExJSG import ShExC
from pyshex import PrefixLibrary, ShExEvaluator
from sparql_slurper import SlurpyGraph
from wikidataintegrator import wdi_core, wdi_helpers
from datetime import datetime


def run_shex_manifest():
    manifest = jsonasobj.loads(requests.get(
        "https://raw.githubusercontent.com/SuLab/Genewiki-ShEx/master/pathways/reactome/manifest.json").text)
    for case in manifest:
        if case.data.startswith("Endpoint:"):
            sparql_endpoint = case.data.replace("Endpoint: ", "")
            schema = requests.get(case.schemaURL).text
            shex = ShExC(schema).schema
            evaluator = ShExEvaluator(schema=shex, debug=True)
            sparql_query = case.queryMap.replace("SPARQL '''", "").replace("'''@START", "")

            df = wdi_core.WDItemEngine.execute_sparql_query(sparql_query)
            for row in df["results"]["bindings"]:
                wdid=row["item"]["value"]
                slurpeddata = SlurpyGraph(sparql_endpoint)
                try:
                        results = evaluator.evaluate(rdf=slurpeddata, focus=wdid, debug=False)
                        for result in results:
                            if result.result:
                                print(str(result.focus) + ": INFO")
                                msg = wdi_helpers.format_msg(wdid, wdid, None, 'CONFORMS', '')

                                wdi_core.WDItemEngine.log("INFO", msg)
                            else:
                                msg = wdi_helpers.format_msg(wdid, wdid, None, '', '')
                                wdi_core.WDItemEngine.log("ERROR", s)


                except RuntimeError:
                    print("Continue after 1 minute, no validation happened on"+ wdid)
                    continue


__metadata__ = {
    'name': 'PathwayBot',
    'maintainer': 'Andra',
    'tags': ['pathways', 'reactome'],
}
log_dir = "./logs"
run_id = datetime.now().strftime('%Y%m%d_%H:%M')
__metadata__['run_id'] = run_id
log_name = '{}-{}.log'.format(__metadata__['name'], run_id)
if wdi_core.WDItemEngine.logger is not None:
    wdi_core.WDItemEngine.logger.handles = []
wdi_core.WDItemEngine.setup_logging(log_dir=log_dir, log_name=log_name, header="",logger_name='reactome')
run_shex_manifest()