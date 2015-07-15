import cPickle
import json
import logging
import sys

from pymachine.definition_parser import read as read_defs
from pymachine.machine import Machine
from pymachine.control import ConceptControl
# from pymachine.utils import MachineGraph

from utils import get_cfg

class Lexicon():
    """A mapping from lemmas to machines"""

    @staticmethod
    def build_from_4lang(cfg):
        fn = cfg.get("machine", "definitions")
        plural_fn = cfg.get("machine", "plurals")
        primitive_fn = cfg.get("machine", "primitives")
        primitives = set(
            [line.decode('utf-8').strip() for line in open(primitive_fn)])
        logging.info('parsing 4lang definitions...')
        definitions = read_defs(file(fn), plural_fn, 0, three_parts=True)
        logging.info('parsed {0} entries, done!'.format(len(definitions)))
        lexicon = Lexicon.create_from_dict(definitions, primitives, cfg)
        return lexicon

    @staticmethod
    def load_from_binary(file_name):
        logging.info('loading lexicon from {0}...'.format(file_name))
        data = cPickle.load(file(file_name))
        machines_dump, ext_machines_dump = map(
            lambda s: json.loads(data[s]), ("def", "ext"))
        cfg, primitives = data['cfg'], data['prim']
        lexicon = Lexicon.create_from_dumps(machines_dump, ext_machines_dump,
                                            primitives, cfg)
        logging.info('done!')
        return lexicon

    def save_to_binary(self, file_name):
        logging.info('saving lexicon to {0}...'.format(file_name))
        data = {
            "def": json.dumps(Lexicon.dump_machines(self.lexicon)),
            "ext": json.dumps(Lexicon.dump_machines(self.ext_lexicon)),
            "prim": self.primitives,
            "cfg": self.cfg}

        with open(file_name, 'w') as out_file:
            cPickle.dump(data, out_file)
        logging.info('done!')

    @staticmethod
    def create_from_dumps(machines_dump, ext_machines_dump, primitives, cfg):
        """builds the lexicon from dumps created by Lexicon.dump_machines"""
        lexicon = Lexicon(cfg)
        lexicon.primitives = primitives
        for word, dumped_machine in machines_dump.iteritems():
            machine = lexicon.get_machine(word, allow_new_base=True)
            lexicon.add_children_from_dump(machine, dumped_machine)

        for word, dumped_machine in ext_machines_dump.iteritems():
            machine = lexicon.get_machine(word, allow_new_ext=True)
            lexicon.add_children_from_dump(machine, dumped_machine)

        return lexicon

    def add_children_from_dump(self, machine, (printname, partitions), depth=0,
                               allow_new_base=False, allow_new_ext=False):
        for i, partition in enumerate(partitions):
            # print "{0}{1}".format("    "*depth, printname)
            # for part in partitions:
                # print "{0}{1}".format("    "*(depth+1), printname)
            for child_name, child_partitions in partition:
                child_pn = child_name.split('_')[0]
                if allow_new_base or allow_new_ext:
                    # these would be cases of expanding while loading
                    child_machine = self.get_machine(
                        child_pn, allow_new_base, allow_new_ext)
                else:
                    child_machine = self.get_new_machine(child_pn)

                self.add_children_from_dump(
                    child_machine, (child_pn, child_partitions), depth+1,
                    allow_new_base, allow_new_ext)
                machine.append(child_machine, i)

    @staticmethod
    def dump_machine(machine, seen=set()):
        name = machine.unique_name()
        seen.add(name)
        return name, [
            [Lexicon.dump_machine(child, seen)
             for child in partition if child.unique_name() not in seen]
            for partition in machine.partitions]

    @staticmethod
    def dump_machines(machines):
        """processes a map of lemmas to machines and dumps them to lists
        of strings, for serialization"""
        dump = {}
        for word, machine_set in machines.iteritems():
            if len(machine_set) > 1:
                raise Exception("cannot dump lexicon with ambiguous \
                    printname: '{0}'".format(word))
            machine = next(iter(machine_set))

            # logging.info('dumping this: {0}'.format(
            #     MachineGraph.create_from_machines([machine]).to_dot()))

            dump[word] = Lexicon.dump_machine(machine)
        return dump

    @staticmethod
    def create_from_dict(word2machine, primitives, cfg):
        lexicon = Lexicon(cfg)
        lexicon.lexicon = dict(word2machine)
        lexicon.primitives = primitives
        return lexicon

    def __init__(self, cfg):
        self.cfg = cfg
        self.lexicon = {}
        self.ext_lexicon = {}
        self.oov_lexicon = {}

    def get_words(self):
        return set(self.lexicon.keys()).union(set(self.ext_lexicon.keys()))

    def add(self, printname, machine, external=True, oov=False):
        if printname in self.oov_lexicon:
            assert oov is False
            del self.oov_lexicon[printname]
        lexicon = self.oov_lexicon if oov else (
            self.ext_lexicon if external else self.lexicon)

        self._add(printname, machine, lexicon)

    def _add(self, printname, machine, lexicon):
        if printname in lexicon:
            raise Exception("duplicate word in lexicon: '{0}'".format(lexicon))
        lexicon[printname] = set([machine])

    def get_new_machine(self, printname):
        """returns a new machine without adding it to any lexicon"""
        return Machine(printname, ConceptControl())

    def get_machine(self, printname, allow_new_base=False,
                    allow_new_ext=False):
        """returns the lowest level (base < ext < oov) existing machine
        for the printname. If none exist, creates a new machine in the lowest
        level allowed by the allow_* flags. Will always create new machines
        for uppercase printnames"""

        # TODO
        if not printname:
            return self.get_machine("_empty_")

        if printname.isupper():
            return self.get_new_machine(printname)

        machines = self.lexicon.get(
            printname, self.ext_lexicon.get(
                printname, self.oov_lexicon.get(printname, set())))
        if len(machines) == 0:
            # logging.info(
            #    u'creating new machine for unknown word: "{0}"'.format(
            #        printname))
            new_machine = Machine(printname, ConceptControl())
            if allow_new_base:
                self.add(printname, new_machine, external=False)
            elif allow_new_ext:
                self.add(printname, new_machine)
            else:
                self.add(printname, new_machine, oov=True)

            return self.get_machine(printname)
        else:
            if len(machines) > 1:
                debug_str = u'ambiguous printname: {0}, machines: {1}'.format(
                    printname,
                    [lex.get(printname, set([]))
                     for lex in (self.lexicon, self.ext_lexicon,
                                 self.oov_lexicon)])
                raise Exception(debug_str)

            return next(iter(machines))

    def expand():
        pass

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s : " +
        "%(module)s (%(lineno)s) - %(levelname)s - %(message)s")
    cfg_file = sys.argv[1] if len(sys.argv) > 1 else None
    cfg = get_cfg(cfg_file)
    lexicon = Lexicon.build_from_4lang(cfg)
    lexicon.save_to_binary(cfg.get("machine", "definitions_binary"))
