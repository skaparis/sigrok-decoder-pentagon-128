from random import sample
import sigrokdecode as srd
# from common.srdhelper import bits2int

class SamplerateError(Exception):
    pass


CH_CPU = 0
CH_C18 = 2
CH_CAS = 3
CH_C3 = 4

SIG_CPU_R = 0
SIG_CPU_F = 1
SIG_CAS_R = 2
SIG_C3_R = 3
SIG_C18_R = 4

class DisplayPhase:
    def __init__(self, begin, end, attr_phase):
        self.begin = begin
        self.end = end
        self.attr_phase = attr_phase


class Decoder(srd.Decoder):
    api_version = 3
    id = 'pentagon-128'
    name = 'Pentagon 128'
    longname = 'Pentagon-128 videocontroller'
    desc = 'Debug pentagon-128 video-controller phases'
    license = 'gplv2+'
    inputs = ['logic']
    outputs = []
    tags = ['dyi/retrocomp', 'RFID']

    channels = (
        {'id': 'cpu', 'name': 'CPU', 'desc': 'CPU Arbiter', 'idn': 'dec_cpu_phase'},
        {'id': 'c2', 'name': 'C2', 'desc': 'Clock', 'idn': 'dec_c2'},
        {'id': 'c18', 'name': 'C18', 'desc': 'Clock', 'idn': 'dec_c18'},
        {'id': 'cas/', 'name': 'CAS/', 'desc': 'CAS', 'idn': 'dec_cas_inv'},
        {'id': 'c3', 'name': 'C3', 'desc': 'C3', 'idn': 'dec_c3'},
    )
    options = (
        # {'id': 'active', 'desc': 'Data lines active level',
        #  'default': 'low', 'values': ('low', 'high'), 'idn':'dec_wiegand_opt_active'},
        # {'id': 'bitwidth_ms', 'desc': 'Single bit width in milliseconds',
        #  'default': 4, 'values': (1, 2, 4, 8, 16, 32), 'idn':'dec_wiegand_opt_bitwidth_ms'},
    )
    annotations = (
        ('state', 'CPU'),
        ('state', 'DIS-AT'),
        ('state', 'DIS-PX'),
        ('bits', 'Visial Bits'),
    )

    ANT_CPU = [0, ['CPU phase', 'CPU']]
    ANT_DIS_PX = [1, ['Display phase - pixels', 'DIS-AT']]
    ANT_DIS_AT = [2, ['Display phase - attributes', 'DIS-PX']]
    ANT_8BITS = [3, ['Display 8 bits', '8bits']]

    annotation_rows = (
        ('state', 'Arbiter state 1', (0, 1, 2)),
        ('bits', 'Binary value', (3,)),
    )

    def __init__(self):
        self.reset()


    def reset(self):
        self.samplerate = None
        self.samples_per_bit = 10

        self.dis_phases = []
        self.begin_cpu = None
        self.last_cas_r = None
        self.dis_attr_phase = True

        self.dis_8bits_begin = None


    def start(self):
        # Register output types and verify user supplied decoder values.
        self.out_ann = self.register(srd.OUTPUT_ANN)


    def metadata(self, key, value):
        # Receive decoder metadata about the data stream.
        if key == srd.SRD_CONF_SAMPLERATE:
            self.samplerate = value
            if self.samplerate:
                ms_per_sample = 1000 * (1.0 / self.samplerate)
                ms_per_bit = float(self.options['bitwidth_ms'])
                self.samples_per_bit = int(max(1, int(ms_per_bit / ms_per_sample)))


    def decode(self):
        if not self.samplerate:
            raise SamplerateError('Cannot decode without samplerate.')

        cas_prev = None
        cas_pos = None

        c3_prev = None
        c3_pos = None

        while True:
            cpu, c2, c18, cas, c3 = self.wait([{CH_CPU: 'r'}, {CH_CPU: 'f'}, {CH_CAS: 'r'}, {CH_C3: 'r'}, {CH_C18: 'r'}])

            if self.matched & ((1 << SIG_CPU_R) | (1 << SIG_CPU_F) | (1 << SIG_CAS_R)| (1 << SIG_C18_R)):
                signals = []
                if self.matched & (1 << SIG_CPU_R):
                    signals.append(SIG_CPU_R)
                if self.matched & (1 << SIG_CPU_F):
                    signals.append(SIG_CPU_F)
                if self.matched & (1 << SIG_CAS_R):
                    signals.append(SIG_CAS_R)
                if self.matched & (1 << SIG_C18_R):
                    signals.append(SIG_C18_R)
                self.on_cas_r_cpu_r(signals)

            if self.matched & (1 << SIG_C3_R):
                self.on_c3_r([SIG_C3_R])


    def on_c3_r(self, signals):
        if self.dis_8bits_begin is not None:
            self.put(self.dis_8bits_begin, self.samplenum, self.out_ann, self.ANT_8BITS)
        self.dis_8bits_begin = self.samplenum

    def append_display_phase(self, being_sample, end_sample):
        dp = DisplayPhase(being_sample, end_sample, self.dis_attr_phase)

        # удлинить последний DIS, если он сформирован только задержкой CPU от CAS в DD15
        if self.dis_phases and (self.dis_phases[-1].end - self.dis_phases[-1].begin) > 2 * (dp.end - dp.begin):
            self.dis_phases[-1].end = dp.end
            return
        else:
            self.dis_phases.append(dp)
        self.dis_attr_phase = not self.dis_attr_phase


    def on_cas_r_cpu_r(self, signals):
        if SIG_C18_R in signals:
            self.dis_attr_phase = False

        if SIG_CPU_R in signals:
            if self.last_cas_r:
                self.append_display_phase(self.last_cas_r, self.samplenum)  # finalize display phase
            self.last_cas_r = None
            self.begin_cpu = self.samplenum
            for dp in self.dis_phases:
                self.put(dp.begin, dp.end, self.out_ann, self.ANT_DIS_PX if dp.attr_phase else self.ANT_DIS_AT)
            self.dis_phases.clear()
            return

        if SIG_CPU_F in signals:
            self.last_cas_r = self.samplenum
            if self.begin_cpu is not None:
                self.put(self.begin_cpu, self.samplenum, self.out_ann, self.ANT_CPU)

        if SIG_CAS_R in signals:
            if self.last_cas_r:
                self.append_display_phase(self.last_cas_r, self.samplenum)
            self.last_cas_r = self.samplenum


    def report(self):
        return '%d samples per bit' % (self.samples_per_bit)
