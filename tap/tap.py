from argparse import ArgumentParser
from collections import OrderedDict
from copy import deepcopy
import json
from pprint import pformat
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Union

from tap.utils import get_class_variables, get_dest, get_git_root, get_git_url, has_git,has_uncommitted_changes,\
    is_option_arg, type_to_str


SUPPORTED_DEFAULT_BASE_TYPES = {str, int, float, bool}
SUPPORTED_DEFAULT_OPTIONAL_TYPES = {Optional[str], Optional[int], Optional[float]}
SUPPORTED_DEFAULT_LIST_TYPES = {List[str], List[int], List[float]}
SUPPORTED_DEFAULT_SET_TYPES = {Set[str], Set[int], Set[float]}
SUPPORTED_DEFAULT_COLLECTION_TYPES = SUPPORTED_DEFAULT_LIST_TYPES | SUPPORTED_DEFAULT_SET_TYPES
SUPPORTED_DEFAULT_TYPES = set.union(SUPPORTED_DEFAULT_BASE_TYPES,
                                    SUPPORTED_DEFAULT_OPTIONAL_TYPES,
                                    SUPPORTED_DEFAULT_COLLECTION_TYPES)


class Tap(ArgumentParser):
    """Tap is a typed argument parser that wraps Python's built-in ArgumentParser."""

    def __init__(self, *args, **kwargs):
        """Initializes the Tap instance.

        :param args: Arguments passed to the super class ArgumentParser.
        :param kwargs: Keyword arguments passed to the super class ArgumentParser.
        """
        # Whether the arguments have been parsed (i.e. if parse_args has been called)
        self._parsed = False

        # Set extra arguments to empty list
        self.extra_args = []

        # Create argument buffer
        self.argument_buffer = OrderedDict()

        # Get help strings from the comments
        self.class_variables = self._get_class_variables()

        # Get annotations from self and all super classes up through tap
        self._annotations = self._get_annotations()

        # Initialize the super class, i.e. ArgumentParser
        super(Tap, self).__init__(*args, **kwargs)

        # Add arguments to self
        self.add_arguments()  # Adds user-overridden arguments to the arguments buffer
        self._add_arguments()  # Adds all arguments in order to self

    def _add_argument(self, *name_or_flags, **kwargs) -> None:
        """Adds an argument to self (i.e. the super class ArgumentParser).

        Sets the following attributes of kwargs when not explicitly provided:
        - type: Set to the type annotation of the argument.
        - default: Set to the default value of the argument (if provided).
        - required: True if a default value of the argument is not provided, False otherwise.
        - action: Set to "store_true" if the argument is a required bool or a bool with default value False.
                  Set to "store_false" if the argument is a bool with default value True.
        - nargs: Set to "*" if the type annotation is List[str], List[int], or List[float].
        - help: Set to the argument documentation from the class docstring.

        :param name_or_flags: Either a name or a list of option strings, e.g. foo or -f, --foo.
        :param kwargs: Keyword arguments.
        """
        # Get variable name
        variable = get_dest(*name_or_flags, **kwargs)

        # Get default if not specified
        if hasattr(self, variable):
            kwargs['default'] = kwargs.get('default', getattr(self, variable))

        # Set required if option arg
        if is_option_arg(*name_or_flags) and variable != 'help':
            kwargs['required'] = kwargs.get('required', not hasattr(self, variable))

        # Set help if necessary
        if 'help' not in kwargs:
            kwargs['help'] = '('

            # Type
            if variable in self._annotations:
                kwargs['help'] += type_to_str(self._annotations[variable]) + ', '

            # Required/default
            if kwargs.get('required', False):
                kwargs['help'] += 'required'
            else:
                kwargs['help'] += f'default={kwargs.get("default", None)}'

            kwargs['help'] += ')'

            # Description
            if variable in self.class_variables:
                kwargs['help'] += ' ' + self.class_variables[variable]['comment']

        # Set other kwargs where not provided
        if variable in self._annotations:
            # Get type annotation
            var_type = self._annotations[variable]

            # If type is not explicitly provided, set it if it's one of our supported default types
            if 'type' not in kwargs:
                if var_type not in SUPPORTED_DEFAULT_TYPES:
                    raise ValueError(
                        f'Variable "{variable}" has type "{var_type}" which is not supported by default.\n'
                        f'Please explicitly add the argument to the parser by writing:\n\n'
                        f'def add_arguments(self) -> None:\n'
                        f'    self.add_argument("--{variable}", type=func, {"required=True" if kwargs["required"] else f"default={getattr(self, variable)}"})\n\n'
                        f'where "func" maps from str to {var_type}.')

                # If Optional type, extract type
                if var_type in SUPPORTED_DEFAULT_OPTIONAL_TYPES:
                    var_type = var_type.__args__[0]

                # If List type, extract type of elements in list and set nargs
                elif var_type in SUPPORTED_DEFAULT_COLLECTION_TYPES:
                    var_type = var_type.__args__[0]
                    kwargs['nargs'] = kwargs.get('nargs', '*')

                # If bool then set action, otherwise set type
                if var_type == bool:
                    kwargs['action'] = kwargs.get('action', f'store_{"true" if kwargs["required"] or not kwargs["default"] else "false"}')
                else:
                    kwargs['type'] = var_type

        super(Tap, self).add_argument(*name_or_flags, **kwargs)

    def add_argument(self, *name_or_flags, **kwargs) -> None:
        """Adds an argument to the argument buffer, which will later be passed to _add_argument."""
        variable = get_dest(*name_or_flags, **kwargs)
        self.argument_buffer[variable] = (name_or_flags, kwargs)

    def _add_arguments(self) -> None:
        """Add arguments to self in the order they are defined as class variables (so the help string is in order)."""
        # Add class variables (in order)
        for variable in self.class_variables:
            if variable in self.argument_buffer:
                name_or_flags, kwargs = self.argument_buffer[variable]
                self._add_argument(*name_or_flags, **kwargs)
            else:
                self._add_argument(f'--{variable}')

        # Add any arguments that were added manually in add_arguments but aren't class variables (in order)
        for variable, (name_or_flags, kwargs) in self.argument_buffer.items():
            if variable not in self.class_variables:
                self._add_argument(*name_or_flags, **kwargs)

    def add_arguments(self) -> None:
        """Explicitly add arguments to the argument buffer if not using default settings."""
        pass

    def process_args(self) -> None:
        """Perform additional argument processing and/or validation."""
        pass

    @staticmethod
    def get_reproducibility_info() -> Dict[str, str]:
        """Gets a dictionary of reproducibility information.

        Reproducibility information always includes:
        - command_line: The command line command used to execute the code.
        - time: The current time.

        If git is installed, reproducibility information also includes:
        - git_root: The root of the git repo where the command is run.
        - git_url: The url of the current hash of the git repo where the command is run.
                   Ex. https://github.com/kswanson-asapp/rationale-alignment/tree/<hash>
        - git_has_uncommitted_changes: Whether the current git repo has uncommitted changes.

        :return: A dictionary of reproducibility information.
        """
        reproducibility = {
            'command_line': f'python {" ".join(sys.argv)}',
            'time': time.strftime('%c')
        }

        if has_git():
            reproducibility['git_root'] = get_git_root()
            reproducibility['git_url'] = get_git_url(commit_hash=True)
            reproducibility['git_has_uncommitted_changes'] = has_uncommitted_changes()

        return reproducibility

    def _log_all(self) -> Dict[str, Any]:
        """Gets all arguments along with reproducibility information.

        :return: A dictionary containing all arguments along with reproducibility information.
        """
        arg_log = self.as_dict()
        arg_log['reproducibility'] = self.get_reproducibility_info()

        return arg_log

    def parse_args(self,
                   args: Optional[Sequence[str]] = None,
                   known_only: bool = False) -> 'Tap':
        """Parses arguments, sets attributes of self equal to the parsed arguments, and processes arguments.

        :param args: List of strings to parse. The default is taken from `sys.argv`.
        :param known_only: If true, ignores extra arguments and only parses known arguments.
        Unparsed arguments are saved to self.extra_args.
        :return: self, which is a Tap instance containing all of the parsed args.
        """
        # Parse args using super class ArgumentParser's parse_args or parse_known_args function
        if known_only:
            default_namespace, self.extra_args = super(Tap, self).parse_known_args(args)
        else:
            default_namespace = super(Tap, self).parse_args(args)

        # Copy parsed arguments to self
        for variable, value in vars(default_namespace).items():
            # Conversion from list to set
            if variable in self._annotations and self._annotations[variable] in SUPPORTED_DEFAULT_SET_TYPES:
                value = set(value)

            # Set variable in self (and deepcopy)
            setattr(self, variable, deepcopy(value))

        # Process args
        self.process_args()

        # Indicate that args have been parsed
        self._parsed = True

        return self

    @classmethod
    def _get_from_self_and_super(cls,
                                 extract_func: Callable[[type], dict],
                                 dict_type: type = dict) -> Union[Dict[str, Any], OrderedDict]:
        """Returns a dictionary mapping variable names to values.

        Variables and values are extracted from classes using key starting
        with this class and traversing up the super classes up through Tap.

        If super class and sub class have the same key, the sub class value is used.

        Super classes are traversed through breadth first search.

        :param extract_func: A function that extracts from a class a dictionary mapping variables to values.
        :param dict_type: The type of dictionary to use (e.g. dict, OrderedDict, etc.)
        :return: A dictionary mapping variable names to values from the class dict.
        """
        visited = set()
        super_classes = [cls]
        dictionary = dict_type()

        while len(super_classes) > 0:
            super_class = super_classes.pop(0)

            if super_class not in visited and issubclass(super_class, Tap):
                super_dictionary = extract_func(super_class)

                # Update only unseen variables to avoid overriding subclass values
                for variable, value in super_dictionary.items():
                    if variable not in dictionary:
                        dictionary[variable] = value
                for variable in super_dictionary.keys() - dictionary.keys():
                    dictionary[variable] = super_dictionary[variable]

                super_classes += list(super_class.__bases__)
                visited.add(super_class)

        return dictionary

    def _get_class_dict(self) -> Dict[str, Any]:
        """Returns a dictionary mapping class variable names to values from the class dict."""
        class_dict = self._get_from_self_and_super(
            extract_func=lambda super_class: dict(getattr(super_class, '__dict__', dict()))
        )
        class_dict = {var: val for var, val in class_dict.items()
                      if not var.startswith('_') and not callable(val) and not isinstance(val, staticmethod)}

        return class_dict

    def _get_annotations(self) -> Dict[str, Any]:
        """Returns a dictionary mapping variable names to their type annotations."""
        return self._get_from_self_and_super(
            extract_func=lambda super_class: dict(getattr(super_class, '__annotations__', dict()))
        )

    def _get_class_variables(self) -> OrderedDict:
        """Returns an OrderedDict mapping class variables names to their additional information."""
        return self._get_from_self_and_super(
            extract_func=lambda super_class: get_class_variables(super_class),
            dict_type=OrderedDict
        )

    def _get_argument_names(self) -> Set[str]:
        """Returns a list of variable names corresponding to the arguments."""
        return set(self._get_class_dict().keys()) | set(self._annotations.keys())

    def as_dict(self) -> Dict[str, Any]:
        """Returns the member variables corresponding to the class variable arguments.

         :return: A dictionary mapping each argument's name to its value.
         """
        if not self._parsed:
            raise ValueError('You should call `parse_args` before retrieving arguments.')

        return {var: getattr(self, var) for var in self._get_argument_names()}

    def save(self, path: str) -> None:
        """Saves the arguments and reproducibility information in JSON format.

        :param path: Path to the JSON file where the arguments will be saved.
        """
        with open(path, 'w') as f:
            json.dump(self._log_all(), f, indent=4, sort_keys=True)

    def __str__(self) -> str:
        """Returns a string representation of self.

        :return: A formatted string representation of the dictionary of all arguments.
        """
        return pformat(self.as_dict())
