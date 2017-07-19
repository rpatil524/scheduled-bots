"""
example human gene
https://www.wikidata.org/wiki/Q14911732
https://mygene.info/v3/gene/1017
https://www.ncbi.nlm.nih.gov/gene/1017
http://uswest.ensembl.org/Homo_sapiens/Gene/Summary?g=ENSG00000123374;r=12:55966769-55972784

example mouse gene
https://www.wikidata.org/wiki/Q21129787

example yeast gene:
https://www.wikidata.org/wiki/Q27539933
https://mygene.info/v3/gene/856615

example microbial gene:
https://www.wikidata.org/wiki/Q23097138
https://mygene.info/v3/gene/7150837

Restructuring this: https://bitbucket.org/sulab/wikidatabots/src/226614eeda5f258fc913b10fdcaa3c22c7f64045/automated_bots/genes/mammals/gene.py?at=jenkins-automation&fileviewer=file-view-default

"""
# TODO: Gene on two chromosomes
# https://www.wikidata.org/wiki/Q20787772

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
import time
from functools import partial

from pymongo import MongoClient
from tqdm import tqdm
import sys
sys.path.insert(0, '/home/gstupp/projects/WikidataIntegrator')
from wikidataintegrator import wdi_login, wdi_core, wdi_helpers

from wikidataintegrator.wdi_fastrun import FastRunContainer

DAYS = 120
from wikidataintegrator.ref_handlers import update_retrieved_if_new
update_retrieved_if_new = partial(update_retrieved_if_new, days=DAYS)


from scheduled_bots.geneprotein import HelperBot, organisms_info, type_of_gene_map, descriptions_by_type
from scheduled_bots.geneprotein.ChromosomeBot import ChromosomeBot
from scheduled_bots.geneprotein.HelperBot import make_ref_source, alwayslist, parse_mygene_src_version, source_items
from scheduled_bots.geneprotein.MicrobeBotResources import get_organism_info, get_all_taxa

try:
    from scheduled_bots.local import WDUSER, WDPASS
except ImportError:
    if "WDUSER" in os.environ and "WDPASS" in os.environ:
        WDUSER = os.environ['WDUSER']
        WDPASS = os.environ['WDPASS']
    else:
        raise ValueError("WDUSER and WDPASS must be specified in local.py or as environment variables")

PROPS = {'found in taxon': 'P703',
         'instance of': 'P31',
         'strand orientation': 'P2548',
         'Entrez Gene ID': 'P351',
         'NCBI Locus tag': 'P2393',
         'Ensembl Gene ID': 'P594',
         'Ensembl Transcript ID': 'P704',
         'genomic assembly': 'P659',
         'genomic start': 'P644',
         'genomic end': 'P645',
         'chromosome': 'P1057',
         'HGNC ID': 'P354',
         'HGNC Gene Symbol': 'P353',
         'RefSeq RNA ID': 'P639',
         'HomoloGene ID': 'P593',
         'Saccharomyces Genome Database ID': 'P3406',
         'Mouse Genome Informatics ID': 'P671',
         'MGI Gene Symbol': 'P2394',
         'Wormbase Gene ID': 'P3860',
         'FlyBase Gene ID': 'P3852',
         'ZFIN Gene ID': 'P3870',
         'Rat Genome Database ID': 'P3853',
         'encodes': 'P688'
         }

__metadata__ = {'name': 'GeneBot',
                'maintainer': 'GSS',
                'tags': ['gene'],
                'properties': list(PROPS.values())
                }

# If the source is "entrez", the reference identifier to be used is "Ensembl Gene ID" (P594)
source_ref_id = {'ensembl': "Ensembl Gene ID",
                 'entrez': 'Entrez Gene ID'}


class Gene:
    """
    Generic gene class. Subclasses: Human, Mammal, Microbe
    """
    record = None
    label = None
    description = None
    aliases = None
    external_ids = dict()
    type_of_gene = None

    def __init__(self, record, organism_info, login):
        """
        generate pbb_core item object

        :param record: dict from mygene,tagged with @value and @source
        :param organism_info: looks like {
            "type": "fungal",
            "name": "Saccharomyces cerevisiae S288c",
            "wdid": "Q27510868",
            'taxid': 559292
        }

        :param login:

        """
        self.record = record
        self.organism_info = organism_info
        self.login = login

        self.wd_item_gene = None
        self.statements = None

    def create_description(self):
        if self.type_of_gene is None:
            raise ValueError("must set type_of_gene first")
        self.description = descriptions_by_type[self.type_of_gene].format(self.organism_info['name'])

    def create_label(self):
        self.label = self.record['name']['@value']

    def create_aliases(self):
        if self.label is None:
            self.create_label()
        aliases = []
        if 'symbol' in self.record:
            aliases.append(self.record['symbol']['@value'])
        if 'name' in self.record:
            aliases.append(self.record['name']['@value'])
        if 'NCBI Locus tag' in self.external_ids:
            aliases.append(self.external_ids['NCBI Locus tag'])
        if 'alias' in self.record:
            aliases.extend(self.record['alias']['@value'])
        aliases = set(aliases) - {self.label} - set(descriptions_by_type.keys())
        self.aliases = list(aliases)

    def set_label_desc_aliases(self, wditem):
        wditem.set_label(self.label)
        curr_descr = wditem.get_description()
        if curr_descr == "" or "of the species" in curr_descr or "gene found in" in curr_descr.lower():
            wditem.set_description(self.description)
        wditem.set_aliases(self.aliases)
        return wditem

    def validate_record(self):
        # handled by HelperBot
        # allow for subclasses to add additional checks
        raise NotImplementedError()

    def parse_external_ids(self):
        ############
        # required external IDs
        ############

        entrez_gene = str(self.record['entrezgene']['@value'])
        external_ids = {'Entrez Gene ID': entrez_gene}
        taxid = self.record['taxid']['@value']

        ############
        # optional external IDs
        ############
        # taxid, example gene
        # mouse: 10090, 102466888
        # rat: 10116, 100362233
        # sgd: 559292, 853415
        # fly: 7227, 31303
        # worm: 6239, 174065
        # zfin: 7955, 368434

        if 'ensembl' in self.record:
            ensembl_gene = self.record['ensembl']['@value']['gene']
            external_ids['Ensembl Gene ID'] = ensembl_gene

        # ncbi locus tag
        if 'locus_tag' in self.record:
            external_ids['NCBI Locus tag'] = self.record['locus_tag']['@value']

        if 'MGI' in self.record:
            external_ids['Mouse Genome Informatics ID'] = self.record['MGI']['@value']
        if 'RGD' in self.record:
            external_ids['Rat Genome Database ID'] = self.record['RGD']['@value']
        if 'SGD' in self.record:
            external_ids['Saccharomyces Genome Database ID'] = self.record['SGD']['@value']
        if 'FLYBASE' in self.record:
            external_ids['FlyBase Gene ID'] = self.record['FLYBASE']['@value']
        if 'WormBase' in self.record:
            external_ids['Wormbase Gene ID'] = self.record['WormBase']['@value']
        if 'ZFIN' in self.record:
            external_ids['ZFIN Gene ID'] = self.record['ZFIN']['@value']

        if 'HGNC' in self.record:
            external_ids['HGNC ID'] = self.record['HGNC']['@value']

        if taxid == 9606 and 'symbol' in self.record and 'HGNC' in self.record:
            # see: https://github.com/stuppie/scheduled-bots/issues/2
            # "and 'HGNC' in record" is required because there is something wrong with mygene
            external_ids['HGNC Gene Symbol'] = self.record['symbol']['@value']

        if taxid == 10090 and 'symbol' in self.record:
            external_ids['MGI Gene Symbol'] = self.record['symbol']['@value']

        if 'homologene' in self.record:
            external_ids['HomoloGene ID'] = str(self.record['homologene']['@value']['id'])

        ############
        # optional external IDs (can have more than one)
        ############
        # Ensembl Transcript ID
        if 'ensembl' in self.record and 'transcript' in self.record['ensembl']['@value']:
            external_ids['Ensembl Transcript ID'] = self.record['ensembl']['@value']['transcript']

        # RefSeq RNA ID
        if 'refseq' in self.record and 'rna' in self.record['refseq']['@value']:
            external_ids['RefSeq RNA ID'] = self.record['refseq']['@value']['rna']

        self.external_ids = external_ids

    def create_statements(self):
        """
        create statements common to all genes
        """
        s = []

        ############
        # ID statements (required)
        ############

        entrez_ref = make_ref_source(self.record['entrezgene']['@source'], PROPS['Entrez Gene ID'],
                                     self.external_ids['Entrez Gene ID'], login=self.login)

        s.append(
            wdi_core.WDString(self.external_ids['Entrez Gene ID'], PROPS['Entrez Gene ID'], references=[entrez_ref]))

        # optional ID statements
        ensembl_ref = None
        if 'Ensembl Gene ID' in self.external_ids:
            ensembl_ref = make_ref_source(self.record['ensembl']['@source'], PROPS['Ensembl Gene ID'],
                                          self.external_ids['Ensembl Gene ID'], login=self.login)
            s.append(wdi_core.WDString(self.external_ids['Ensembl Gene ID'], PROPS['Ensembl Gene ID'],
                                       references=[ensembl_ref]))
            # no ensembl transcript ID unless ensembl gene is there also
            if 'Ensembl Transcript ID' in self.external_ids:
                for id in self.external_ids['Ensembl Transcript ID']:
                    s.append(wdi_core.WDString(id, PROPS['Ensembl Transcript ID'], references=[ensembl_ref]))

        key = 'RefSeq RNA ID'
        if key in self.external_ids:
            for id in self.external_ids[key]:
                s.append(wdi_core.WDString(id, PROPS[key], references=[entrez_ref]))

        for key in ['NCBI Locus tag', 'Saccharomyces Genome Database ID', 'Mouse Genome Informatics ID',
                    'MGI Gene Symbol', 'HomoloGene ID', 'Rat Genome Database ID', 'FlyBase Gene ID',
                    'Wormbase Gene ID', 'ZFIN Gene ID']:
            if key in self.external_ids:
                s.append(wdi_core.WDString(self.external_ids[key], PROPS[key], references=[entrez_ref]))

        ############
        # Gene statements
        ############
        # if there is an ensembl ID, this comes from ensembl, otherwise, entrez
        gene_ref = ensembl_ref if ensembl_ref is not None else entrez_ref

        # instance of gene, ncRNA.. etc
        type_of_gene = self.record['type_of_gene']['@value']
        assert type_of_gene in type_of_gene_map, "unknown type of gene: {}".format(type_of_gene)
        self.type_of_gene = type_of_gene
        s.append(wdi_core.WDItemID(type_of_gene_map[type_of_gene], PROPS['instance of'], references=[gene_ref]))

        if type_of_gene_map[type_of_gene] != "Q7187":
            # make sure we add instance of "gene" as well
            s.append(wdi_core.WDItemID("Q7187", PROPS['instance of'], references=[gene_ref]))

        # found in taxon
        s.append(wdi_core.WDItemID(self.organism_info['wdid'], PROPS['found in taxon'], references=[gene_ref]))

        return s

    def create_item(self, fast_run=True, write=True):
        try:
            self.parse_external_ids()
            self.statements = self.create_statements()
            self.create_label()
            self.create_description()
            self.create_aliases()

            self.fast_run_base_filter = {PROPS['Entrez Gene ID']: '', PROPS['found in taxon']: self.organism_info['wdid']}

            self.wd_item_gene = wdi_core.WDItemEngine(item_name=self.label, domain='genes', data=self.statements,
                                                      append_value=[PROPS['instance of']],
                                                      fast_run=fast_run, fast_run_base_filter=self.fast_run_base_filter,
                                                      fast_run_use_refs=True, ref_handler=update_retrieved_if_new,
                                                      global_ref_mode="CUSTOM")

            self.wd_item_gene = self.set_label_desc_aliases(self.wd_item_gene)
            wdi_helpers.try_write(self.wd_item_gene, self.external_ids['Entrez Gene ID'], PROPS['Entrez Gene ID'],
                                  self.login, write=write)

        except Exception as e:
            exc_info = sys.exc_info()
            traceback.print_exception(*exc_info)
            msg = wdi_helpers.format_msg(self.external_ids['Entrez Gene ID'], PROPS['Entrez Gene ID'], None,
                                         str(e), msg_type=type(e))
            wdi_core.WDItemEngine.log("ERROR", msg)

class MicrobeGene(Gene):
    """
    Microbes

    """

    def __init__(self, record, organism_info, login):
        super().__init__(record, organism_info, login)

    def create_label(self):
        self.label = self.record['name']['@value'] + " " + self.record['locus_tag']['@value']

    def create_description(self):
        if self.organism_info['type']:
            self.description = '{} gene found in {}'.format(self.organism_info['type'], self.organism_info['name'])
        else:
            self.description = 'Gene found in {}'.format(self.organism_info['name'])

    def validate_record(self):
        pass

    def create_statements(self):
        # create generic gene statements
        s = super().create_statements()

        # add on gene position statements
        s.extend(self.create_gp_statements())

        return s

    def create_gp_statements(self):
        """
        Create genomic_pos start stop orientation no chromosome
        :return:
        """
        genomic_pos_value = self.record['genomic_pos']['@value'][0]
        genomic_pos_source = self.record['genomic_pos']['@source']
        genomic_pos_id_prop = source_ref_id[genomic_pos_source['id']]
        genomic_pos_ref = make_ref_source(genomic_pos_source, PROPS[genomic_pos_id_prop],
                                          self.external_ids[genomic_pos_id_prop], login=self.login)

        s = []

        # create qualifier for chromosome REFSEQ ID (not chrom item)
        chromosome = genomic_pos_value['chr']
        rs_chrom = wdi_core.WDString(value=chromosome, prop_nr='P2249', is_qualifier=True)

        # strand orientation
        strand_orientation = 'Q22809680' if genomic_pos_value['strand'] == 1 else 'Q22809711'
        s.append(wdi_core.WDItemID(strand_orientation, PROPS['strand orientation'],
                                   references=[genomic_pos_ref], qualifiers=[rs_chrom]))
        # genomic start and end
        s.append(wdi_core.WDString(str(int(genomic_pos_value['start'])), PROPS['genomic start'],
                                   references=[genomic_pos_ref], qualifiers=[rs_chrom]))
        s.append(wdi_core.WDString(str(int(genomic_pos_value['end'])), PROPS['genomic end'],
                                   references=[genomic_pos_ref], qualifiers=[rs_chrom]))

        return s


class EukaryoticGene(Gene):
    """
    yeast, mouse, rat, worm, fly, zebrafish
    """

    def __init__(self, record, organism_info, chr_num_wdid, login):
        """
        :param chr_num_wdid: mapping of chr number (str) to wdid
        """
        super().__init__(record, organism_info, login)
        self.chr_num_wdid = chr_num_wdid

    def create_label(self):
        self.label = self.record['symbol']['@value']

    def create_statements(self):

        # create generic gene statements
        s = super().create_statements()

        # add on gene position statements
        if 'genomic_pos' in self.record:
            s.extend(self.create_gp_statements_chr())

        return s

    def create_gp_statements_chr(self):
        """
        Create genomic_pos start stop orientation on a chromosome
        :return:
        """
        genomic_pos_values = self.record['genomic_pos']['@value']
        genomic_pos_source = self.record['genomic_pos']['@source']
        genomic_pos_id_prop = source_ref_id[genomic_pos_source['id']]
        genomic_pos_ref = make_ref_source(genomic_pos_source, PROPS[genomic_pos_id_prop],
                                          self.external_ids[genomic_pos_id_prop], login=self.login)

        all_chr = set([self.chr_num_wdid[x['chr']] for x in genomic_pos_values])
        all_strand = set(['Q22809680' if x['strand'] == 1 else 'Q22809711' for x in genomic_pos_values])

        s = []
        for genomic_pos_value in genomic_pos_values:
            # create qualifier for start/stop/orientation
            chrom_wdid = self.chr_num_wdid[genomic_pos_value['chr']]
            qualifiers = [wdi_core.WDItemID(chrom_wdid, PROPS['chromosome'], is_qualifier=True)]

            # genomic start and end
            s.append(wdi_core.WDString(str(int(genomic_pos_value['start'])), PROPS['genomic start'],
                                       references=[genomic_pos_ref], qualifiers=qualifiers))
            s.append(wdi_core.WDString(str(int(genomic_pos_value['end'])), PROPS['genomic end'],
                                       references=[genomic_pos_ref], qualifiers=qualifiers))

        for chr in all_chr:
            s.append(wdi_core.WDItemID(chr, PROPS['chromosome'], references=[genomic_pos_ref]))

        if len(all_strand) == 1:
            # todo: not sure what to do if you have both orientations on the same chr
            strand_orientation = list(all_strand)[0]
            s.append(wdi_core.WDItemID(strand_orientation, PROPS['strand orientation'], references=[genomic_pos_ref]))

        return s


class HumanGene(EukaryoticGene):
    def create_statements(self):
        # create gene statements
        s = Gene.create_statements(self)
        entrez_ref = make_ref_source(self.record['entrezgene']['@source'], PROPS['Entrez Gene ID'],
                                     self.external_ids['Entrez Gene ID'], login=self.login)

        # add on human specific gene statements
        for key in ['HGNC ID', 'HGNC Gene Symbol']:
            if key in self.external_ids:
                s.append(wdi_core.WDString(self.external_ids[key], PROPS[key], references=[entrez_ref]))

        # add on gene position statements
        if 'genomic_pos' in self.record:
            s.extend(self.do_gp_human())

        return s

    def validate_record(self):
        assert 'locus_tag' in self.record
        assert 'HGNC' in self.record
        assert 'symbol' in self.record
        assert 'ensembl' in self.record and 'transcript' in self.record['ensembl']
        assert 'refseq' in self.record and 'rna' in self.record['ensembl']
        assert 'alias' in self.record

    def do_gp_human(self):
        """
        create genomic pos, chr, strand statements for human
        includes genomic assembly

        genes that are on an unlocalized scaffold will have no genomic position statements
        example: https://mygene.info/v3/gene/102724770
        https://www.wikidata.org/wiki/Q20970159
        :return:
        """
        genomic_pos_values = self.record['genomic_pos']['@value']
        genomic_pos_source = self.record['genomic_pos']['@source']
        genomic_pos_id_prop = source_ref_id[genomic_pos_source['id']]
        genomic_pos_ref = make_ref_source(genomic_pos_source, PROPS[genomic_pos_id_prop],
                                          self.external_ids[genomic_pos_id_prop], login=self.login)
        assembly_hg38 = wdi_core.WDItemID("Q20966585", PROPS['genomic assembly'], is_qualifier=True)

        for x in genomic_pos_values:
            x['assembly'] = 'hg38'

        do_hg19 = False
        if 'genomic_pos_hg19' in self.record:
            do_hg19 = True
            genomic_pos_value_hg19 = self.record['genomic_pos_hg19']['@value']
            genomic_pos_source_hg19 = self.record['genomic_pos_hg19']['@source']
            genomic_pos_id_prop_hg19 = source_ref_id[genomic_pos_source_hg19['id']]
            genomic_pos_ref_hg19 = make_ref_source(genomic_pos_source_hg19, PROPS[genomic_pos_id_prop_hg19],
                                                   self.external_ids[genomic_pos_id_prop_hg19], login=self.login)
            assembly_hg19 = wdi_core.WDItemID("Q21067546", PROPS['genomic assembly'], is_qualifier=True)
            # combine all together
            for x in genomic_pos_value_hg19:
                x['assembly'] = 'hg19'
            genomic_pos_values.extend(genomic_pos_value_hg19)

        # remove those where we don't know the chromosome
        genomic_pos_values = [x for x in genomic_pos_values if x['chr'] in self.chr_num_wdid]
        # print(len(genomic_pos_values))

        all_chr = set([self.chr_num_wdid[x['chr']] for x in genomic_pos_values])
        all_strand = set(['Q22809680' if x['strand'] == 1 else 'Q22809711' for x in genomic_pos_values])

        s = []
        for genomic_pos_value in genomic_pos_values:

            # create qualifiers (chromosome and assembly)
            chrom_wdid = self.chr_num_wdid[genomic_pos_value['chr']]
            qualifiers = [wdi_core.WDItemID(chrom_wdid, PROPS['chromosome'], is_qualifier=True)]
            if genomic_pos_value['assembly'] == 'hg38':
                qualifiers.append(assembly_hg38)
                ref = genomic_pos_ref
            elif genomic_pos_value['assembly'] == 'hg19':
                qualifiers.append(assembly_hg19)
                ref = genomic_pos_ref_hg19

            # genomic start and end
            s.append(wdi_core.WDString(str(int(genomic_pos_value['start'])), PROPS['genomic start'],
                                       references=[ref], qualifiers=qualifiers))
            s.append(wdi_core.WDString(str(int(genomic_pos_value['end'])), PROPS['genomic end'],
                                       references=[ref], qualifiers=qualifiers))

        # strand orientations
        # if the same for all, only put one statement
        if len(all_strand) == 1 and do_hg19:
            strand_orientation = list(all_strand)[0]
            s.append(wdi_core.WDItemID(strand_orientation, PROPS['strand orientation'],
                                       references=[genomic_pos_ref], qualifiers=[assembly_hg38, assembly_hg19]))
        elif len(all_strand) == 1 and not do_hg19:
            strand_orientation = list(all_strand)[0]
            s.append(wdi_core.WDItemID(strand_orientation, PROPS['strand orientation'],
                                       references=[genomic_pos_ref], qualifiers=[assembly_hg38]))

        # chromosome
        # if the same for all, only put one statement
        if do_hg19 and len(all_chr) == 1:
            chrom_wdid = list(all_chr)[0]
            s.append(wdi_core.WDItemID(chrom_wdid, PROPS['chromosome'],
                                       references=[genomic_pos_ref], qualifiers=[assembly_hg38, assembly_hg19]))
        elif len(all_chr) == 1 and not do_hg19:
            chrom_wdid = list(all_chr)[0]
            s.append(wdi_core.WDItemID(chrom_wdid, PROPS['chromosome'],
                                       references=[genomic_pos_ref], qualifiers=[assembly_hg38]))

        # print(s)
        return s


class GeneBot:
    """
    Generic genebot class
    """
    GENE_CLASS = Gene
    item = None

    def __init__(self, organism_info, login):
        self.login = login
        self.organism_info = organism_info

    def run(self, records, total=None, fast_run=True, write=True):
        records = self.filter(records)
        for record in tqdm(records, mininterval=2, total=total):
            gene = self.GENE_CLASS(record, self.organism_info, self.login)
            self.item = gene.create_item(fast_run=fast_run, write=write)

    def filter(self, records):
        """
        This is used to selectively skip certain records based on conditions within the record or to specifically
        alter certain fields before sending to the Bot
        """
        # If we are processing zebrafish records, skip the record if it doesn't have a zfin ID
        for record in records:
            if record['taxid']['@value'] == 7955 and 'ZFIN' not in record:
                continue
            else:
                yield record

    def cleanup(self, releases, last_updated):
        entrez_wdid = wdi_helpers.id_mapper('P351', ((PROPS['found in taxon'], self.organism_info['wdid']),))
        filter = {PROPS['Entrez Gene ID']: '', PROPS['found in taxon']: self.organism_info['wdid']}
        frc = FastRunContainer(wdi_core.WDBaseDataType, wdi_core.WDItemEngine, base_filter=filter, use_refs=True)
        frc.clear()
        for qid in tqdm(entrez_wdid.values()):
            remove_deprecated_statements(qid, frc, releases, last_updated, list(PROPS.values()), self.login)


class MammalianGeneBot(GeneBot):
    GENE_CLASS = EukaryoticGene

    def __init__(self, organism_info, chr_num_wdid, login):
        super().__init__(organism_info, login)
        self.chr_num_wdid = chr_num_wdid

    def run(self, records, total=None, fast_run=True, write=True):
        records = self.filter(records)
        for record in tqdm(records, mininterval=2, total=total):
            # print(record['entrezgene'])
            gene = self.GENE_CLASS(record, self.organism_info, self.chr_num_wdid, self.login)
            gene.create_item(fast_run=fast_run, write=write)

class HumanGeneBot(MammalianGeneBot):
    GENE_CLASS = HumanGene


class MicrobeGeneBot(GeneBot):
    GENE_CLASS = MicrobeGene


def remove_deprecated_statements(qid, frc, releases, last_updated, props, login):
    """
    :param qid: qid of item
    :param frc: a fastrun container
    :param releases: list of releases to remove (a statement that has a reference that is stated in one of these
            releases will be removed)
    :param last_updated: looks like {'Q20641742': datetime.date(2017,5,6)}. a statement that has a reference that is
            stated in Q20641742 (entrez) and was retrieved more than DAYS before 2017-5-6 will be removed
    :param props: look at these props
    :param login:
    :return:
    """
    for prop in props:
        frc.write_required([wdi_core.WDString("fake value", prop)])
    orig_statements = frc.reconstruct_statements(qid)
    releases = set(int(r[1:]) for r in releases)

    s_dep = []
    for s in orig_statements:
        if any(any(x.get_prop_nr() == 'P248' and x.get_value() in releases for x in r) for r in s.get_references()):
            setattr(s, 'remove', '')
            s_dep.append(s)
        else:
            for r in s.get_references():
                dbs = [x.get_value() for x in r if x.get_value() in last_updated]
                if dbs:
                    db = dbs[0]
                    if any(x.get_prop_nr() == 'P813' and last_updated[db] - x.get_value() > DAYS for x in r):
                        setattr(s, 'remove', '')
                        s_dep.append(s)
    if s_dep:
        print("-----")
        print(qid)
        print(len(s_dep))
        print([(x.get_prop_nr(), x.value) for x in s_dep])
        print([(x.get_references()[0]) for x in s_dep])
        wd_item = wdi_core.WDItemEngine(wd_item_id=qid, domain='none', data=s_dep, fast_run=False)
        wdi_helpers.try_write(wd_item, '', '', login, edit_summary="remove deprecated statements")

def main(coll, taxid, metadata, log_dir="./logs", run_id=None, fast_run=True, write=True, doc_filter=None):
    """
    Main function for creating/updating genes

    :param coll: mongo collection containing gene data from mygene
    :type coll: pymongo.collection.Collection
    :param taxid: taxon to use (ncbi tax id)
    :type taxid: str
    :param metadata: looks like: {"ensembl" : 84, "cpdb" : 31, "netaffy" : "na35", "ucsc" : "20160620", .. }
    :type metadata: dict
    :param log_dir: dir to store logs
    :type log_dir: str
    :param fast_run: use fast run mode
    :type fast_run: bool
    :param write: actually perform write
    :type write: bool
    :param doc_filter: Override the doc_filter for determining which docs to write. Useful for testing
    :type doc_filter: dict
    :return: None
    """

    # make sure the organism is found in wikidata
    taxid = int(taxid)
    organism_wdid = wdi_helpers.prop2qid("P685", str(taxid))
    if not organism_wdid:
        print("organism {} not found in wikidata".format(taxid))
        return None

    # login
    login = wdi_login.WDLogin(user=WDUSER, pwd=WDPASS)
    if wdi_core.WDItemEngine.logger is not None:
        wdi_core.WDItemEngine.logger.handles = []
        wdi_core.WDItemEngine.logger.handlers = []

    run_id = run_id if run_id is not None else datetime.now().strftime('%Y%m%d_%H:%M')
    log_name = '{}-{}.log'.format(__metadata__['name'], run_id)
    __metadata__['taxid'] = taxid
    wdi_core.WDItemEngine.setup_logging(log_dir=log_dir, logger_name='WD_logger', log_name=log_name,
                                        header=json.dumps(__metadata__))

    # get organism metadata (name, organism type, wdid)
    if taxid in organisms_info and organisms_info[taxid]['type'] != "microbial":
        validate_type = 'eukaryotic'
        organism_info = organisms_info[taxid]
        # make sure all chromosome items are found in wikidata
        cb = ChromosomeBot()
        chr_num_wdid = cb.get_or_create(organism_info, login=login)
        if int(organism_info['taxid']) == 9606:
            bot = HumanGeneBot(organism_info, chr_num_wdid, login)
        else:
            bot = MammalianGeneBot(organism_info, chr_num_wdid, login)
    else:
        # check if its one of the microbe refs
        # raises valueerror if not...
        organism_info = get_organism_info(taxid)
        print(organism_info)
        bot = MicrobeGeneBot(organism_info, login)
        validate_type = "microbial"

    # only do certain records
    doc_filter = doc_filter if doc_filter is not None else {'taxid': taxid, 'entrezgene': {'$exists': True}}
    docs = coll.find(doc_filter).batch_size(20)
    total = docs.count()
    print("total number of records: {}".format(total))
    docs = HelperBot.validate_docs(docs, validate_type, PROPS['Entrez Gene ID'])
    records = HelperBot.tag_mygene_docs(docs, metadata)

    bot.run(records, total=total, fast_run=fast_run, write=write)
    time.sleep(10 * 60)
    releases = dict()
    releases_to_remove = set()
    last_updated = dict()
    metadata = {k:v for k,v in metadata.items() if k in {'uniprot', 'ensembl', 'entrez'}}
    for k,v in parse_mygene_src_version(metadata).items():
        if "release" in v:
            if k not in releases:
                releases[k] = wdi_helpers.id_mapper('P393', (('P629', source_items[k]),))
            to_remove = set(releases[k].values())
            to_remove.discard(releases[k][v['release']])
            releases_to_remove.update(to_remove)
            print("{}: Removing releases: {}, keeping release: {}".format(k, ", ".join(set(releases[k]) - {v['release']}), v['release']))
        else:
            last_updated[source_items[k]] = datetime.strptime(v["timestamp"], "%Y%m%d")
    print(last_updated)
    bot.cleanup(releases_to_remove, last_updated)

if __name__ == "__main__":
    """
    Data to be used is stored in a mongo collection. collection name: "mygene"
    """
    parser = argparse.ArgumentParser(description='run wikidata gene bot')
    parser.add_argument('--log-dir', help='directory to store logs', type=str)
    parser.add_argument('--dummy', help='do not actually do write', action='store_true')
    parser.add_argument('--taxon',
                        help="only run using this taxon (ncbi tax id). or 'microbe' for all microbes. comma separated",
                        type=str, required=True)
    parser.add_argument('--mongo-uri', type=str, default="mongodb://localhost:27017")
    parser.add_argument('--mongo-db', type=str, default="wikidata_src")
    parser.add_argument('--fastrun', dest='fastrun', action='store_true')
    parser.add_argument('--no-fastrun', dest='fastrun', action='store_false')
    parser.set_defaults(fastrun=True)
    args = parser.parse_args()
    log_dir = args.log_dir if args.log_dir else "./logs"
    run_id = datetime.now().strftime('%Y%m%d_%H:%M')
    __metadata__['run_id'] = run_id
    taxon = args.taxon
    fast_run = args.fastrun
    coll = MongoClient(args.mongo_uri)[args.mongo_db]["mygene"]

    # get metadata about sources
    # this should be stored in the same db under the collection: mygene_sources
    metadata_coll = MongoClient(args.mongo_uri)[args.mongo_db]["mygene_sources"]
    assert metadata_coll.count() == 1
    metadata = metadata_coll.find_one()

    if "microbe" in taxon:
        microbe_taxa = get_all_taxa()
        taxon = taxon.replace("microbe", ','.join(map(str, microbe_taxa)))

    for taxon1 in taxon.split(","):
        main(coll, taxon1, metadata, run_id=run_id, log_dir=log_dir, fast_run=fast_run, write=not args.dummy)
        # done with this run, clear fast run container to save on RAM
        wdi_core.WDItemEngine.fast_run_store = []
        wdi_core.WDItemEngine.fast_run_container = None
