from tempfile import TemporaryDirectory
import collections
import os
import pandas as pd
import numpy as np
from mir_eval import separation
from untwist import (data, transforms, utilities)
from . import anchor
import matlab_wrapper


def load_audio(df,
               force_mono=False,
               start=None,
               end=None):

    if isinstance(df, pd.Series):
        df = df.to_frame()

    out = {}
    for item in df.iterrows():
        wav = data.audio.Wave.read(item[1]['filepath'])
        if force_mono:
            wav = wav.as_mono()

        if start and end:
            wav = segment(wav, start, end)

        key = '{0}-{1}'.format(item[1]['method'], item[1]['target'])
        out[key] = wav
    return out


def find_active_portion(wave, duration, perc=90):
    '''
    Returns the start and end sample indices of an active portion of the audio
    file according to the Pth percentile of the windowed energy measurements.
    '''

    window_size = int(np.round(wave.sample_rate * duration))
    hop_size = window_size // 4
    framer = transforms.stft.Framer(window_size, hop_size,
                                    False, False)
    frames = framer.process(wave.as_mono())
    energy = (frames * frames).mean(1)

    select_frame = np.argmin(np.abs(energy - np.percentile(energy, perc)))

    start = select_frame * hop_size
    end = start + window_size

    return start, end


def segment(wave, start, end, ramp_dur=0.02):

    wave = wave[start:end]

    ramp_dur_samples = int(np.round(ramp_dur * wave.sample_rate))

    t = np.linspace(0, np.pi / 2.0, ramp_dur_samples).reshape(-1, 1)
    wave[:ramp_dur_samples] *= np.sin(t) ** 2
    wave[-ramp_dur_samples:] *= np.cos(t) ** 2

    return wave


def write_mixtures_from_sample(sample,
                               target='vocals',
                               directory=None,
                               force_mono=True,
                               target_loudness=-23,
                               mixing_levels=[-12, -6, 0, 6, 12],
                               segment_duration=7,
                               save_sources=False):

    # Iterate over the tracks and write audio out:
    for idx, g_sample in sample.groupby('track_id'):

        # Prepare saving of audio
        folder = '{0}-{1}-{2}'.format(
            'mix',
            g_sample.iloc[0]['track_id'],
            g_sample.iloc[0]['metric'])

        full_path = os.path.join(directory, folder)

        if not os.path.exists(full_path):
            os.makedirs(full_path)

        '''
        Reference audio
        '''

        ref_sample = g_sample[g_sample.method == 'ref']

        # Reference target
        ref = load_audio(ref_sample[ref_sample.target == target],
                         force_mono)

        # Find portion of track to take
        (ref_key, ref_audio), = ref.items()
        start, end = find_active_portion(ref_audio, segment_duration, 75)
        target_audio = segment(ref_audio, start, end)

        # Reference non-target stems
        others = load_audio(
            ref_sample[ref_sample.target != target],
            force_mono,
            start,
            end)
        accomp_audio = sum(other for name, other in others.items())

        # Reference and anchor mixes
        for level in mixing_levels:

            name = 'ref_mix_{}dB'.format(level)
            new_target = utilities.conversion.db_to_amp(level) * target_audio
            mix = new_target + accomp_audio
            level_dif = write_wav(mix, os.path.join(full_path, name + '.wav'),
                                  target_loudness)

            if save_sources:

                write_wav(
                    new_target * utilities.conversion.db_to_amp(level_dif),
                    os.path.join(full_path, name + '_target.wav'),
                    None)

                write_wav(
                    accomp_audio * utilities.conversion.db_to_amp(level_dif),
                    os.path.join(full_path, name + '_accomp.wav'),
                    None)

            creator = anchor.RemixAnchor(
                    new_target,
                    accomp_audio,
                    trim_factor_distorted=0.2,
                    trim_factor_artefacts=0.99,
                    target_level_offset=-14,
                    quality_anchor_loudness_balance=[0, 0])

            anchors = creator.create()

            for anchor_type in anchors._fields:

                if anchor_type == 'Interferer':
                    name = 'anchor_loudness_mix_{}dB'.format(level)
                elif anchor_type == 'Quality':
                    name = 'anchor_quality_mix_{}dB'.format(level)
                else:
                    continue

                wav = getattr(anchors, anchor_type)

                dif = write_wav(wav,
                                os.path.join(full_path, name + '.wav'),
                                target_loudness)

                if (anchor_type == 'Interferer') and save_sources:

                    anchor_tgt, anchor_accomp = creator.interferer_anchor_both_sources()

                    write_wav(anchor_tgt * utilities.conversion.db_to_amp(dif),
                              os.path.join(full_path, name + '_target.wav'),
                              None)

                    write_wav(anchor_accomp * utilities.conversion.db_to_amp(dif),
                              os.path.join(full_path, name + '_accomp.wav'),
                              None)

        # Mixes per method
        not_ref_sample = g_sample[g_sample.method != 'ref']
        for method_name, method_sample in not_ref_sample.groupby('method'):

            # Get target and accompaniment
            index = method_sample['target'] == target

            target_audio = load_audio(method_sample[index],
                                      force_mono, start, end)

            (_, target_audio), = target_audio.items()

            if 'accompaniment' in method_sample['target'].values:

                index = method_sample['target'] == 'accompaniment'

                accompaniments = load_audio(method_sample[index],
                                            force_mono, start, end)

                (_, accomp), = accompaniments.items()

            else:

                others = load_audio(
                    method_sample[method_sample.target != target],
                    force_mono,
                    start,
                    end)

                accomp = sum(other for name, other in others.items())

            # Fix GRA
            if method_name in ['GRA2', 'GRA3']:
                target_audio = -1 * target_audio
                accomp = -1 * accomp

            # Mixing
            for level in mixing_levels:

                name = '{0}_mix_{1}dB'.format(method_name, level)

                new_target = utilities.conversion.db_to_amp(level) * target_audio
                mix = new_target + accomp

                level_dif = write_wav(mix,
                                      os.path.join(full_path, name + '.wav'),
                                      target_loudness)

                if save_sources:

                    write_wav(
                        new_target * utilities.conversion.db_to_amp(level_dif),
                        os.path.join(full_path, name + '_target.wav'),
                        None)

                    write_wav(
                        accomp * utilities.conversion.db_to_amp(level_dif),
                        os.path.join(full_path, name + '_accomp.wav'),
                        None)


def write_target_from_sample(sample,
                             target='vocals',
                             directory=None,
                             force_mono=True,
                             target_loudness=-26,
                             segment_duration=7,
                             song_start_and_end_times=None,
                             trim_factor_distorted=0.2,
                             include_background_in_quality_anchor=True,
                             loudness_normalise_interferer=True,
                             suffix=None,
                             overall_gain=0,
                             ):
    '''
    (More doc needed)
    This function will also write the accompaniment (sum of non-target stems)
    out to `ref_accompaniment.wav', with the same gain factor as applied to the
    reference (target). This means that:
        mixture_2 = ref.wav + ref_accompaniment.wav
    where mixture_2 = mixture * some_gain_factor

    If you do not want to loudness normalise stimuli, set `target_loudness' to
    None.
    '''

    # Iterate over the tracks and write audio out:
    for idx, g_sample in sample.groupby('track_id'):

        ref_sample = g_sample[g_sample.method == 'ref']

        # Reference target
        ref_sample_target = ref_sample[ref_sample.target == target]
        ref = load_audio(ref_sample_target, force_mono)

        # Find portion of track to take
        current_track = str(ref_sample_target.track_id.values[0])
        (ref_key, ref_audio), = ref.items()
        if (isinstance(song_start_and_end_times, dict) and
           current_track in song_start_and_end_times.keys()):
            start = song_start_and_end_times[current_track][0]
            if len(song_start_and_end_times[current_track]) == 2:
                end = song_start_and_end_times[current_track][1]
            else:
                end = start + segment_duration
            start = int(np.round(start * ref_audio.sample_rate))
            end = int(np.round(end * ref_audio.sample_rate))
        else:
            start, end = find_active_portion(ref_audio, segment_duration, 75)
        ref[ref_key] = segment(ref_audio, start, end)

        # Reference non-target stems
        others = load_audio(ref_sample[ref_sample.target != target],
                            force_mono,
                            start,
                            end)

        list_of_others = list(others.values())

        # Load test items at the same point in time (same segment times)
        test_items = load_audio(g_sample[(g_sample.method != 'ref') &
                                         (g_sample.target == target)],
                                force_mono,
                                start,
                                end)

        # Generate anchors
        anchor_creator = anchor.Anchor(
            ref[ref_key],
            list_of_others,
            trim_factor_distorted=trim_factor_distorted,
            include_background_in_quality_anchor=include_background_in_quality_anchor,
            loudness_normalise_interferer=loudness_normalise_interferer,
        )

        anchors = anchor_creator.create()

        # Write audio
        folder = '{0}-{1}-{2}'.format(
            target,
            g_sample.iloc[0]['track_id'],
            g_sample.iloc[0]['metric'])

        full_path = os.path.join(directory, folder)

        if not os.path.exists(full_path):
            os.makedirs(full_path)

        for name, wav in ref.items():

            name = name.split('-')[0]  # Remove target name

            if suffix:
                name += suffix

            # The reference
            dif = write_wav(wav, os.path.join(full_path, name + '.wav'),
                            target_loudness, overall_gain)

            # The accompaniment
            write_wav(sum(list_of_others) *
                      utilities.conversion.db_to_amp(dif),
                      os.path.join(full_path, name + '_accompaniment.wav'),
                      None, overall_gain)

        # Write out the other stems
        for name, wav in others.items():

            name = name.split('-')[1]

            if suffix:
                name += suffix

            write_wav(wav * utilities.conversion.db_to_amp(dif),
                      os.path.join(full_path, name + '.wav'),
                      None, overall_gain)

        for name, wav in test_items.items():
            name = name.split('-')[0]
            if suffix:
                name += suffix
            write_wav(wav, os.path.join(full_path, name + '.wav'),
                      target_loudness, overall_gain)

        for name in anchors._fields:

            wav = getattr(anchors, name)

            if suffix:
                name += suffix

            write_wav(wav, os.path.join(full_path, name + '.wav'),
                      target_loudness, overall_gain)


def write_wav(sig, filename, target_loudness=None, overall_gain=0):

    if target_loudness:
        level_dif = target_loudness - sig.loudness
        sig.loudness = target_loudness
    else:
        level_dif = 0

    sig *= utilities.db_to_amp(overall_gain)

    # If you need 32-bit wavs, use
    sig = sig.astype('float32')
    sig.write(filename)

    return level_dif


def combine_anchors(distortion, artefact):
    gain = utilities.conversion.db_to_amp(
        distortion.loudness - artefact.loudness)
    return 0.7 * distortion + 0.3 * gain * artefact


def make_waves_same_length(list_of_waves):

    min_length = np.min([_.num_frames for _ in list_of_waves])
    min_length = np.minimum(min_length,
                            np.min([_.num_frames for _ in list_of_waves]))

    for i, wave in enumerate(list_of_waves):
        list_of_waves[i] = wave[:min_length]

    return list_of_waves


def bss_eval(list_of_ref_waves, list_of_est_waves):
    '''
    This function computed the Bss Eval measures given the reference and
    estimated sources, both of which should be mono untwist.data.audio.Wave
    objects. I will trim the end of your audio if they are not equal in length.

    You must give me a list of waves or 1 wave per argument.

    Returns:
        BssEvalStats named tuple with the field names:
            - sdr: Signal to Distortion Ratio
            - sir: Signal to Interference Ratio
            - sar: Signal to Artefacts Ratio
            - perm: Best ordering of estimated sources in the mean SIR sense
    '''

    BssEvalStats = collections.namedtuple('BssEvalStats', 'sdr sir sar perm')

    if isinstance(list_of_ref_waves, data.audio.Wave):
        list_of_ref_waves = [list_of_ref_waves]
    if isinstance(list_of_est_waves, data.audio.Wave):
        list_of_est_waves = [list_of_est_waves]

    if not isinstance(list_of_ref_waves, list):
        raise ValueError('I want a list of waves!')

    num_sources = len(list_of_ref_waves)
    if len(list_of_est_waves) != num_sources:
        raise ValueError('The number of reference and estimates sources is not equal')

    list_of_ref_waves[0].check_mono()

    waves = make_waves_same_length(list_of_ref_waves + list_of_est_waves)
    ref_sources = np.array([_[:, 0] for _ in waves[:num_sources]])
    est_sources = np.array([_[:, 0] for _ in waves[num_sources:]])

    (sdr, sir, sar, perm) = separation.bss_eval_sources(ref_sources,
                                                        est_sources,
                                                        False)

    return BssEvalStats(sdr=sdr,
                        sir=sir,
                        sar=sar,
                        perm=perm)


def peass(list_of_ref_waves, list_of_est_waves, path_to_peass_toolbox):
    '''
    This function computed the Bss Eval measures given the reference and
    estimated sources, both of which should be mono untwist.data.audio.Wave
    objects. I will trim the end of your audio if they are not equal in length.

    You must give me a list of waves or 1 wave per argument.

    Returns:
        BssEvalStats named tuple with the field names:
            - sdr: Signal to Distortion Ratio
            - sir: Signal to Interference Ratio
            - sar: Signal to Artefacts Ratio
            - perm: Best ordering of estimated sources in the mean SIR sense
    '''

    main_script = '''
        options.segmentationFactor = 1;
        res = PEASS_ObjectiveMeasure(refFiles, estimateFile, options);
        ops = res.OPS;
        tps = res.TPS;
        ips = res.IPS;
        aps = res.APS;
        '''

    StatsPEASS = collections.namedtuple('StatsPEASS', ['ops',
                                                       'tps',
                                                       'ips',
                                                       'aps',
                                                       ]
                                        )

    # Initial setup for dealing with waves
    if isinstance(list_of_ref_waves, data.audio.Wave):
        list_of_ref_waves = [list_of_ref_waves]
    if isinstance(list_of_est_waves, data.audio.Wave):
        list_of_est_waves = [list_of_est_waves]

    if not isinstance(list_of_ref_waves, list):
        raise ValueError('I want a list of waves!')

    num_sources = len(list_of_ref_waves)
    if len(list_of_est_waves) != num_sources:
        raise ValueError('The number of reference and estimates sources is not equal')

    list_of_ref_waves[0].check_mono()

    waves = make_waves_same_length(list_of_ref_waves + list_of_est_waves)
    refs = waves[:num_sources]
    ests = waves[num_sources:]

    # Matlab side... (yak!)
    matlab = matlab_wrapper.MatlabSession()
    matlab.eval("addpath(genpath('{}'));".format(path_to_peass_toolbox))

    with TemporaryDirectory() as tmp_dir:

        # First we need to write the reference source to disk
        for i, wave in enumerate(refs):
            name = '{}/{}.wav'.format(tmp_dir, i)
            wave.write(name)
            if i == 0:
                cell = "{{'{0}'".format(name)
            else:
                cell = "{0};'{1}'".format(cell, name)
        cell += '}'

        matlab.eval('originalFiles = {};'.format(cell))
        matlab.eval("options.destDir = '{}'".format(tmp_dir))

        # Now run PEASS on the estimated sources
        stats = []
        for i, wave in enumerate(ests):

            organised_files = (
                "refFiles = "
                "originalFiles([{},"
                "setdiff(1:length(originalFiles), {})]);").format(i+1, i+1)

            matlab.eval(organised_files)

            name = '{}/est.wav'.format(tmp_dir)
            wave.write(name)

            matlab.put('estimateFile', name)

            matlab.eval(main_script)

            stats.append(
                StatsPEASS(ops=matlab.get('ops'),
                           tps=matlab.get('tps'),
                           ips=matlab.get('ips'),
                           aps=matlab.get('aps'))
            )

    if len(stats) == 1:
        return stats[0]
    else:
        return stats
