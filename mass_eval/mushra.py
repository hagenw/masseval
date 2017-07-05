import os
from lxml import etree

from . import config


def mushra_mixture_from_track_sample(sample,
                                     directory,
                                     target_loudness=-23,
                                     mixing_levels=[0, 6, 12]):

    sample = sample[sample['method'] != 'Ref']

    if not os.path.exists(directory):
        os.makedirs(directory)

    # Create different configuration files for every question
    for question_id, question in config.mushra_questions.items():

        # Start config file for the MUSHRA test
        xsi_namespace = 'http://www.w3.org/2001/XMLSchema-instance'
        xsi = '{%s}' % xsi_namespace
        nsmap = {None: xsi_namespace}  # the default namespace (no prefix)
        # <waet>
        waet = etree.Element(xsi + 'waet', nsmap=nsmap)  # lxml only!
        waet.set(xsi + 'noNamespaceSchemaLocation', 'test-schema.xsd')

        # <setup>
        setup = etree.SubElement(waet, 'setup',
                                 interface='MUSHRA',
                                 projectReturn='save.php',
                                 randomiseOrder='true',
                                 crossFade='0.01',
                                 loudness='-23')
        #   <exitText/>
        etree.SubElement(setup, 'exitText').text = config.mushra_exit_message
        #   <metric>
        metric = etree.SubElement(setup, 'metric')
        for value in config.mushra_metric:
            etree.SubElement(metric, 'metricenable').text = value
        #   </metric>
        #   <interface>
        interface = etree.SubElement(setup, 'interface')
        for entry in config.mushra_interface:
            iface_option = etree.SubElement(interface, 'interfaceoption')
            for option in sorted(entry):
                iface_option.set(option, entry[option])
        #   </interface>
        # </setup>

        for level in mixing_levels:

            # Iterate over the tracks and write audio out:
            for g_sample in sample.groupby('track_id'):

                folder = '{0}-{1}-{2}'.format(
                        'mix',
                        g_sample[1].iloc[0]['track_id'],
                        g_sample[1].iloc[0]['metric'])

                # <page>
                page = etree.SubElement(waet, 'page')
                page_id = folder.replace('mix', question_id) + \
                    '_' + str(level) + 'dB'
                page.set('id', page_id)
                page.set('hostURL', 'stim/' + folder + '/')
                for option in sorted(config.mushra_page):
                    page.set(option, config.mushra_page[option])

                #   <interface>
                page_interface = etree.SubElement(page, 'interface')
                #       <title/>
                etree.SubElement(page_interface, 'title').text = \
                    question['title']
                #       <scales>
                scales = etree.SubElement(page_interface, 'scales')
                for label in sorted(question['scale']):
                    scale = etree.SubElement(scales, 'scalelabel')
                    scale.text = question['scale'][label]
                    scale.set('position', label)
                #       </scales>
                #   </interface>

                #   <audioelement/>
                ref = etree.SubElement(page, 'audioelement')
                ref.set('url', 'ref_mix_' + str(level) + 'dB.wav')
                ref.set('id', page_id + '_refout')
                ref.set('type', 'outside-reference')
                hidden_ref = etree.SubElement(page, 'audioelement')
                hidden_ref.set('url', 'ref_mix_' + str(level) + 'dB.wav')
                hidden_ref.set('id', page_id + '_ref')
                hidden_ref.set('type', 'reference')
                anchor = etree.SubElement(page, 'audioelement')
                anchor_file = 'anchor_' + question_id + '_mix_' + \
                    str(level) + 'dB.wav'
                anchor.set('url', anchor_file)
                anchor.set('id', page_id + '_anchor')
                anchor.set('type', 'anchor')
                for method in g_sample[1]['method'].unique():
                    alg = etree.SubElement(page, 'audioelement')
                    alg.set('url', method + '_mix_' + str(level) + 'dB.wav')
                    alg.set('id', page_id + '_' + method)
                # </page>
                # </waet>

        tree = etree.ElementTree(waet)
        filename = os.path.join(directory,
                                config.mushra_testname + '_' + question_id + '.xml')
        tree.write(filename,
                   pretty_print=True,
                   xml_declaration=True,
                   encoding='UTF-8')

        # Workaround to remove one xml namespace entry
        with open(filename, 'r') as file:
            filedata = file.read()

        xmlns_string = 'xmlns="http://www.w3.org/2001/XMLSchema-instance" '
        filedata = filedata.replace(xmlns_string, '')

        with open(filename, 'w') as file:
            file.write(filedata)


if __name__ == '__main__':

    import mass_eval
    import pandas as pd
    import numpy as np

    mass_eval.config.mus_base_path = '/vol/vssp/maruss/data2/MUS2017'
    mass_eval.config.dsd_base_path = '/vol/vssp/datasets/audio/DSD100'

    df = mass_eval.data.get_sisec_df()

    exclude_tracks = []
    mix_sample = pd.DataFrame()
    for metric in ['SDR', 'SAR', 'SIR']:

        sample = mass_eval.data.get_sample(
                df,
                num_tracks=2,
                num_algos=4,
                metric=metric,
                target='vocals',
                only_these_algos=None,
                exclude_tracks=exclude_tracks,
                selection_plot=False)

        tracks = sample['track_id'].values
        exclude_tracks = np.append(exclude_tracks, np.unique(tracks))
        mix_sample = pd.concat([mix_sample, sample])

    mushra_mixture_from_track_sample(mix_sample,
                                     '.',
                                     target_loudness=-26,
                                     mixing_levels=[0, 6, 12])
